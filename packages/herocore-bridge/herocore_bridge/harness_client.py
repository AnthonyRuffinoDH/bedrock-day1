"""
herocore_bridge.harness_client
===============================
WebSocket (and HTTP fallback) client used by the Slack harness to invoke
AgentCore and stream back the response.

Extracted from: bot.py
  invoke_agentcore_ws, invoke_agentcore, invoke_agentcore_auto, _http_url_to_ws

Key changes vs the original:
  - ``cognito_token_fn`` is now an explicit callable parameter rather than a
    module-level import from bot.py.  This makes the module fully stateless and
    testable in isolation.
  - ``client_tools`` is an explicit parameter; callers pass the list returned by
    :func:`herocore_bridge.config.load_mcp_tools`.
  - The routing table is built lazily inside the call so callers don't need to
    manage module-level state.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("herocore_bridge.harness_client")

WS_TIMEOUT = int(os.environ.get("WS_TIMEOUT", "120"))


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def http_url_to_ws(url: str) -> str:
    """Convert an AgentCore HTTP endpoint URL to its WebSocket equivalent.

    Replaces a ``/invocations`` path suffix with ``/ws``, or appends ``/ws``
    if no such suffix exists.

    Examples
    --------
    >>> http_url_to_ws("https://example.execute-api.us-east-1.amazonaws.com/prod/invocations")
    'wss://example.execute-api.us-east-1.amazonaws.com/prod/ws'
    """
    parsed = urlparse(url)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    path = parsed.path.rstrip("/")
    if path.endswith("/invocations"):
        path = path[: -len("/invocations")] + "/ws"
    else:
        path = path + "/ws"
    return f"{ws_scheme}://{parsed.netloc}{path}"


# ---------------------------------------------------------------------------
# WebSocket invocation
# ---------------------------------------------------------------------------

def invoke_agentcore_ws(
    payload: dict[str, Any],
    channel_id: str,
    thread_ts: str,
    *,
    cognito_token_fn: Callable[[], str],
    agent_url: str,
    session_id: str,
    client_tools: list[dict[str, Any]] | None = None,
    include_user_id: bool = False,
    timeout: int = WS_TIMEOUT,
) -> str:
    """Invoke AgentCore over a WebSocket connection.

    Parameters
    ----------
    payload:
        The JSON payload to send (e.g. ``{"prompt": "Hello"}``).
    channel_id:
        Slack channel ID — used as the Custom-User-Id header when
        ``include_user_id`` is True.
    thread_ts:
        Slack thread timestamp — used to build the runtime session ID.
    cognito_token_fn:
        A zero-argument callable that returns a fresh Cognito Bearer token
        string.  Called once per invocation.
    agent_url:
        AgentCore HTTP endpoint URL.  Converted to a WebSocket URL internally.
    session_id:
        Opaque session identifier forwarded as the runtime session header.
    client_tools:
        Optional list of tool dicts (from :func:`load_mcp_tools`) whose
        schemas will be injected into the payload so AgentCore knows which
        harness-side tools are available.
    include_user_id:
        Whether to forward ``channel_id`` as the AgentCore custom user ID
        header (used for gate evaluation calls).
    timeout:
        WebSocket connection + receive timeout in seconds.

    Returns
    -------
    str
        The full streamed text response from AgentCore.
    """
    import websocket as ws_client  # websocket-client

    from herocore_bridge.config import build_tool_routing_table
    from herocore_bridge.executor import execute_mcp_tool

    token = cognito_token_fn()
    ws_url = http_url_to_ws(agent_url)

    headers = {
        "Authorization": f"Bearer {token}",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
    }
    if include_user_id:
        headers["X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Id"] = channel_id

    logger.info(
        "[WS OUTBOUND] Connecting to AgentCore WebSocket. "
        "SlackThread: %s | RuntimeSessionId: %s | Action: %s",
        thread_ts, session_id, payload.get("action", "standard_invoke"),
    )

    conn = ws_client.create_connection(
        ws_url,
        header=[f"{k}: {v}" for k, v in headers.items()],
        timeout=timeout,
    )

    # Build routing table for tool dispatch
    routing_table: dict[str, dict[str, Any]] = {}
    if client_tools:
        routing_table = build_tool_routing_table(client_tools)
        schemas = [
            {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
            for t in client_tools
        ]
        payload = {**payload, "client_tools": schemas}
        logger.info("[MCP INJECT] Injected %d client tool schemas into payload.", len(schemas))

    try:
        conn.send(json.dumps(payload))

        full_response = ""
        while True:
            try:
                raw = conn.recv()
            except ws_client.WebSocketConnectionClosedException:
                logger.error("[WS DISCONNECT] Connection closed unexpectedly during receive.")
                return full_response.strip() if full_response else (
                    "Sorry, the connection to the server was lost. Please try again."
                )

            if not raw:
                break
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "text_delta":
                full_response += msg.get("content", "")

            elif msg_type == "done":
                break

            elif msg_type == "tool_call":
                tool_call_id = msg.get("tool_call_id")
                tool_name = msg.get("name")
                tool_args = msg.get("args", {})
                logger.info("[WS TOOL_CALL] Received tool_call: %s (id=%s)", tool_name, tool_call_id)
                content = execute_mcp_tool(tool_name, tool_args, routing_table)
                logger.info(
                    "[WS TOOL_RESULT] Returning result for %s (id=%s): %s...",
                    tool_name, tool_call_id, content[:80],
                )
                try:
                    conn.send(json.dumps({
                        "type": "tool_result",
                        "tool_call_id": tool_call_id,
                        "content": content,
                    }))
                except ws_client.WebSocketConnectionClosedException:
                    logger.error("[WS DISCONNECT] Connection closed while sending tool_result.")
                    return "Sorry, the connection to the server was lost during tool execution. Please try again."

            elif msg_type == "error":
                logger.error("[WS ERROR] AgentCore returned error: %s", msg.get("content"))
                return f"Sorry, something went wrong: {msg.get('content', 'unknown error')}"

    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    logger.info("[WS RESPONSE] Received %d chars from AgentCore.", len(full_response))
    result = full_response.strip()
    if not result:
        logger.warning("[WS RESPONSE] Empty response from AgentCore — possible agent error.")
        return "Sorry, I wasn't able to generate a response. Please try again."
    return result


# ---------------------------------------------------------------------------
# HTTP (streaming SSE) fallback
# ---------------------------------------------------------------------------

def invoke_agentcore_http(
    payload: dict[str, Any],
    channel_id: str,
    thread_ts: str,
    *,
    cognito_token_fn: Callable[[], str],
    agent_url: str,
    session_id: str,
    include_user_id: bool = False,
) -> str:
    """Invoke AgentCore via HTTP streaming (SSE ``data:`` lines).

    This is the non-WebSocket fallback path, equivalent to the original
    ``invoke_agentcore`` function in ``bot.py``.
    """
    import requests  # already a dep

    token = cognito_token_fn()

    req_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
    }
    if include_user_id:
        req_headers["X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Id"] = channel_id

    logger.info(
        "[HTTP OUTBOUND] Calling AgentCore API. "
        "SlackThread: %s | RuntimeSessionId: %s | Action: %s",
        thread_ts, session_id, payload.get("action", "standard_invoke"),
    )

    response = requests.post(agent_url, headers=req_headers, json=payload, stream=True)
    response.raise_for_status()

    full_response = ""
    for line in response.iter_lines():
        if line:
            decoded = line.decode("utf-8")
            if decoded.startswith("data: "):
                try:
                    full_response += json.loads(decoded[6:])
                except json.JSONDecodeError:
                    continue

    logger.info("[HTTP RESPONSE] Received %d chars from AgentCore.", len(full_response))
    return full_response.strip()
