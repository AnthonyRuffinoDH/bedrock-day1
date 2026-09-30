"""
herocore_bridge.executor
========================
Executes MCP tool calls against local MCP servers via:
  - stdio subprocess (JSON-RPC over stdin/stdout)
  - HTTP Streamable (POST JSON-RPC, plain or SSE response)

Extracted from: bot.py
  _execute_mcp_tool, _execute_mcp_tool_stdio, _execute_mcp_tool_http,
  _parse_rpc_response, _parse_sse_response
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

logger = logging.getLogger("herocore_bridge.executor")

MCP_TOOL_TIMEOUT = int(os.environ.get("MCP_TOOL_TIMEOUT", "30"))


def execute_mcp_tool(
    tool_name: str,
    args: dict[str, Any],
    routing_table: dict[str, dict[str, Any]],
    timeout: int = MCP_TOOL_TIMEOUT,
) -> str:
    """Execute a tool call against its configured local MCP server.

    Parameters
    ----------
    tool_name:
        The name of the tool to invoke (must exist in ``routing_table``).
    args:
        Tool input arguments as a plain dict.
    routing_table:
        Mapping of tool name → server config, as produced by
        :func:`herocore_bridge.config.build_tool_routing_table`.
    timeout:
        Per-call timeout in seconds.

    Returns
    -------
    str
        The tool result text, or an ``"Error: ..."`` string on failure.
    """
    server = routing_table.get(tool_name)
    if not server:
        return f"Error: no MCP server configured for tool '{tool_name}'"

    transport = server.get("transport", "stdio")
    logger.info("[MCP EXEC] Calling tool '%s' via %s (transport=%s)", tool_name, server["name"], transport)

    if transport == "http":
        return _execute_mcp_tool_http(tool_name, args, server, timeout)
    return _execute_mcp_tool_stdio(tool_name, args, server, timeout)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _execute_mcp_tool_stdio(
    tool_name: str,
    args: dict[str, Any],
    server: dict[str, Any],
    timeout: int,
) -> str:
    """Execute a tool call via stdio subprocess JSON-RPC."""
    import subprocess  # stdlib

    if not server.get("command"):
        return f"Error: no command configured for stdio server '{server['name']}'"

    rpc_request = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": args},
    })

    try:
        result = subprocess.run(
            server["command"],
            input=rpc_request,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            logger.warning(
                "[MCP EXEC] Server %s exited with code %d: %s",
                server["name"], result.returncode, result.stderr[:200],
            )
            return f"Error: MCP server exited with code {result.returncode}: {result.stderr[:200]}"
        return parse_rpc_response(result.stdout)

    except subprocess.TimeoutExpired:
        logger.warning("[MCP EXEC] Tool '%s' timed out after %ds", tool_name, timeout)
        return f"Error: tool execution timed out after {timeout}s"
    except FileNotFoundError:
        logger.error("[MCP EXEC] Command not found: %s", server["command"])
        return f"Error: MCP server command not found: {server['command'][0]}"
    except Exception as exc:  # noqa: BLE001
        logger.error("[MCP EXEC] Unexpected error executing tool '%s': %s", tool_name, exc)
        return f"Error: {exc}"


def _execute_mcp_tool_http(
    tool_name: str,
    args: dict[str, Any],
    server: dict[str, Any],
    timeout: int,
) -> str:
    """Execute a tool call via MCP Streamable HTTP (POST JSON-RPC to server URL)."""
    from herocore_bridge.auth import resolve_auth_headers

    url = server.get("url")
    if not url:
        return f"Error: no url configured for HTTP server '{server['name']}'"

    rpc_request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": args},
    }

    auth_headers = resolve_auth_headers(server.get("auth"))
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        **auth_headers,
    }

    try:
        resp = requests.post(url, json=rpc_request, headers=headers, timeout=timeout)
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "")
        if "text/event-stream" in content_type:
            return parse_sse_response(resp.text)
        return parse_rpc_response(resp.text)

    except requests.Timeout:
        logger.warning("[MCP EXEC] HTTP tool '%s' timed out after %ds", tool_name, timeout)
        return f"Error: tool execution timed out after {timeout}s"
    except requests.RequestException as exc:
        logger.error("[MCP EXEC] HTTP error calling tool '%s': %s", tool_name, exc)
        return f"Error: {exc}"
    except Exception as exc:  # noqa: BLE001
        logger.error("[MCP EXEC] Unexpected error executing tool '%s': %s", tool_name, exc)
        return f"Error: {exc}"


def parse_rpc_response(raw: str) -> str:
    """Parse a JSON-RPC 2.0 response and extract the MCP ``content`` text."""
    try:
        rpc_response = json.loads(raw)
        if "error" in rpc_response:
            return f"Error: {rpc_response['error'].get('message', 'unknown MCP error')}"
        rpc_result = rpc_response.get("result", {})
        content_parts = rpc_result.get("content", [])
        return "\n".join(p.get("text", str(p)) for p in content_parts) if content_parts else str(rpc_result)
    except json.JSONDecodeError:
        return raw.strip() or "Error: empty response from MCP server"


def parse_sse_response(raw: str) -> str:
    """Parse an SSE stream and extract the first ``data:`` event as JSON-RPC."""
    for line in raw.split("\n"):
        if line.startswith("data: "):
            data = line[6:].strip()
            if data:
                return parse_rpc_response(data)
    return "Error: no data event in SSE response"
