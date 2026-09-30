"""AgentCore invocation clients (HTTP and WebSocket)."""
from __future__ import annotations

import json
import logging
from typing import Callable
from urllib.parse import urlparse

import requests

from .executor import execute_mcp_tool

logger = logging.getLogger("herocore_bridge.harness_client")


def invoke_agentcore_http(
    payload: dict,
    agent_url: str,
    cognito_token_fn: Callable[[], str],
    session_id: str,
    user_id: str | None = None,
) -> str:
    """Invoke AgentCore via HTTP streaming. Returns the full response text."""
    token = cognito_token_fn()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
    }

    if user_id:
        headers["X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Id"] = user_id

    logger.info(
        f"[HTTP OUTBOUND] Calling AgentCore API. "
        f"RuntimeSessionId: {session_id} | "
        f"Action: {payload.get('action', 'standard_invoke')}"
    )

    response = requests.post(
        agent_url,
        headers=headers,
        json=payload,
        stream=True,
    )

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

    logger.info(
        f"[HTTP RESPONSE] Received {len(full_response)} bytes from AgentCore."
    )
    return full_response.strip()


def http_url_to_ws(url: str) -> str:
    """Convert an AgentCore HTTP URL to its WebSocket equivalent."""
    parsed = urlparse(url)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    path = parsed.path.rstrip("/")
    if path.endswith("/invocations"):
        path = path[:-len("/invocations")] + "/ws"
    else:
        path = path + "/ws"
    return f"{ws_scheme}://{parsed.netloc}{path}"


def invoke_agentcore_ws(
    payload: dict,
    agent_url: str,
    cognito_token_fn: Callable[[], str],
    session_id: str,
    user_id: str | None = None,
    client_tools: list[dict] | None = None,
    tool_executor: Callable | None = None,
    routing_table: dict | None = None,
) -> str:
    """Invoke AgentCore via WebSocket. Returns the full response text.

    Args:
        payload: The request payload to send.
        agent_url: The HTTP URL of the AgentCore endpoint (will be converted to WS).
        cognito_token_fn: Callable returning a fresh Cognito access token.
        session_id: The session ID header value.
        user_id: Optional user ID header value.
        client_tools: Optional list of client tool schema dicts to inject into payload.
        tool_executor: Callable(tool_name, args, routing_table) for executing tool calls.
            Defaults to execute_mcp_tool from executor module.
        routing_table: Tool routing table passed to tool_executor.
    """
    import websocket as ws_client

    if tool_executor is None:
        tool_executor = execute_mcp_tool

    token = cognito_token_fn()
    ws_url = http_url_to_ws(agent_url)

    headers = {
        "Authorization": f"Bearer {token}",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
    }
    if user_id:
        headers["X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Id"] = user_id

    logger.info(
        f"[WS OUTBOUND] Connecting to AgentCore WebSocket. "
        f"RuntimeSessionId: {session_id} | "
        f"Action: {payload.get('action', 'standard_invoke')}"
    )

    conn = ws_client.create_connection(
        ws_url,
        header=[f"{k}: {v}" for k, v in headers.items()],
        timeout=120,
    )

    try:
        if client_tools:
            schemas = [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in client_tools]
            payload["client_tools"] = schemas
            logger.info(f"[MCP INJECT] Injected {len(schemas)} client tool schemas into payload.")

        conn.send(json.dumps(payload))

        full_response = ""
        while True:
            try:
                raw = conn.recv()
            except ws_client.WebSocketConnectionClosedException:
                logger.error("[WS DISCONNECT] Connection closed unexpectedly during receive.")
                if full_response:
                    return full_response.strip()
                return "Sorry, the connection to the server was lost. Please try again."

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
                logger.info(f"[WS TOOL_CALL] Received tool_call: {tool_name} (id={tool_call_id})")
                content = tool_executor(tool_name, tool_args, routing_table or {})
                logger.info(f"[WS TOOL_RESULT] Returning result for {tool_name} (id={tool_call_id}): {content[:80]}...")
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
                logger.error(f"[WS ERROR] AgentCore returned error: {msg.get('content')}")
                return f"Sorry, something went wrong: {msg.get('content', 'unknown error')}"
    finally:
        try:
            conn.close()
        except Exception:
            pass

    logger.info(f"[WS RESPONSE] Received {len(full_response)} bytes from AgentCore.")
    result = full_response.strip()
    if not result:
        logger.warning("[WS RESPONSE] Empty response from AgentCore — possible agent error.")
        return "Sorry, I wasn't able to generate a response. Please try again."
    return result
