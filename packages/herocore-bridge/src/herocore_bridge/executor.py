"""MCP tool execution via stdio subprocess or HTTP."""
from __future__ import annotations

import json
import logging

import requests

from .auth import resolve_auth_headers

logger = logging.getLogger("herocore_bridge.executor")


def execute_mcp_tool(tool_name: str, args: dict, routing_table: dict, timeout: int = 30) -> str:
    """Execute a tool call against its configured local MCP server via stdio or HTTP."""
    server = routing_table.get(tool_name)
    if not server:
        return f"Error: no MCP server configured for tool '{tool_name}'"

    transport = server.get("transport", "stdio")
    logger.info(f"[MCP EXEC] Calling tool '{tool_name}' via {server['name']} (transport={transport})")

    if transport == "http":
        return _execute_mcp_tool_http(tool_name, args, server, timeout)
    return _execute_mcp_tool_stdio(tool_name, args, server, timeout)


def _execute_mcp_tool_stdio(tool_name: str, args: dict, server: dict, timeout: int) -> str:
    """Execute a tool call via stdio subprocess JSON-RPC."""
    import subprocess

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
            logger.warning(f"[MCP EXEC] Server {server['name']} exited with code {result.returncode}: {result.stderr[:200]}")
            return f"Error: MCP server exited with code {result.returncode}: {result.stderr[:200]}"

        return _parse_rpc_response(result.stdout)

    except subprocess.TimeoutExpired:
        logger.warning(f"[MCP EXEC] Tool '{tool_name}' timed out after {timeout}s")
        return f"Error: tool execution timed out after {timeout}s"
    except FileNotFoundError:
        logger.error(f"[MCP EXEC] Command not found: {server['command']}")
        return f"Error: MCP server command not found: {server['command'][0]}"
    except Exception as e:
        logger.error(f"[MCP EXEC] Unexpected error executing tool '{tool_name}': {e}")
        return f"Error: {e}"


def _execute_mcp_tool_http(tool_name: str, args: dict, server: dict, timeout: int) -> str:
    """Execute a tool call via MCP Streamable HTTP (POST JSON-RPC to server URL)."""
    url = server.get("url")
    if not url:
        return f"Error: no url configured for HTTP server '{server['name']}'"

    auth_headers, auth_error = resolve_auth_headers(server)
    if auth_error:
        logger.warning(f"[MCP EXEC] Auth resolution failed for tool '{tool_name}': {auth_error}")
        return auth_error

    rpc_request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": args},
    }

    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    headers.update(auth_headers)

    try:
        resp = requests.post(
            url,
            json=rpc_request,
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "")
        if "text/event-stream" in content_type:
            return _parse_sse_response(resp.text)

        return _parse_rpc_response(resp.text)

    except requests.Timeout:
        logger.warning(f"[MCP EXEC] HTTP tool '{tool_name}' timed out after {timeout}s")
        return f"Error: tool execution timed out after {timeout}s"
    except requests.RequestException as e:
        logger.error(f"[MCP EXEC] HTTP error calling tool '{tool_name}': {e}")
        return f"Error: {e}"
    except Exception as e:
        logger.error(f"[MCP EXEC] Unexpected error executing tool '{tool_name}': {e}")
        return f"Error: {e}"


def _parse_rpc_response(raw: str) -> str:
    """Parse a JSON-RPC response and extract MCP content."""
    try:
        rpc_response = json.loads(raw)
        if "error" in rpc_response:
            return f"Error: {rpc_response['error'].get('message', 'unknown MCP error')}"
        rpc_result = rpc_response.get("result", {})
        content_parts = rpc_result.get("content", [])
        return "\n".join(p.get("text", str(p)) for p in content_parts) if content_parts else str(rpc_result)
    except json.JSONDecodeError:
        return raw.strip() or "Error: empty response from MCP server"


def _parse_sse_response(raw: str) -> str:
    """Parse an SSE stream for the JSON-RPC response event."""
    for line in raw.split("\n"):
        if line.startswith("data: "):
            data = line[6:].strip()
            if data:
                return _parse_rpc_response(data)
    return "Error: no data event in SSE response"
