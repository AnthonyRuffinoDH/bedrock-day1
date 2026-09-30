"""AgentCore-side WebSocket proxy tools for client tool execution.

This module imports strands-agents at module level and is only usable
when the [agentcore] extra is installed.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from strands.types.tools import AgentTool, ToolUse, ToolSpec
from strands.types._events import ToolResultEvent
from starlette.websockets import WebSocketDisconnect

logger = logging.getLogger("herocore_bridge.agent_proxy")

CLIENT_NS = "client"

WS_TOOL_TIMEOUT = 30


class WebSocketProxyTool(AgentTool):
    """A tool that proxies calls over WebSocket to the harness for local MCP execution.
    Registered under the CLIENT_NS namespace; uses the original name on the wire."""

    def __init__(self, name: str, description: str, input_schema: dict, websocket):
        super().__init__()
        self._original_name = name
        self._name = f"{CLIENT_NS}__{name}"
        self._description = description
        self._input_schema = input_schema
        self._websocket = websocket

    @property
    def tool_name(self) -> str:
        return self._name

    @property
    def tool_spec(self) -> ToolSpec:
        return {
            "name": self._name,
            "description": self._description,
            "inputSchema": {"json": self._input_schema},
        }

    @property
    def tool_type(self) -> str:
        return "function"

    async def stream(self, tool_use: ToolUse, invocation_state: dict[str, Any], **kwargs: Any):
        tool_use_id = tool_use.get("toolUseId", "unknown")
        tool_input = tool_use.get("input", {})
        call_id = str(uuid.uuid4())

        logger.info(f"[WS TOOL PROXY] Sending tool_call: {self._original_name} (id={call_id})")
        try:
            await self._websocket.send_json({
                "type": "tool_call",
                "tool_call_id": call_id,
                "name": self._original_name,
                "args": tool_input,
            })
            result_msg = await asyncio.wait_for(
                self._websocket.receive_json(), timeout=WS_TOOL_TIMEOUT
            )
        except asyncio.TimeoutError:
            logger.warning(f"[WS TOOL PROXY] Timeout waiting for tool_result: {self._name} (id={call_id})")
            yield ToolResultEvent({"toolUseId": tool_use_id, "status": "error", "content": [{"text": f"Error: tool execution timed out after {WS_TOOL_TIMEOUT}s"}]})
            return
        except WebSocketDisconnect:
            logger.warning(f"[WS TOOL PROXY] Client disconnected: {self._name} (id={call_id})")
            yield ToolResultEvent({"toolUseId": tool_use_id, "status": "error", "content": [{"text": "Error: client disconnected during tool execution"}]})
            return
        except Exception as e:
            logger.error(f"[WS TOOL PROXY] Error proxying tool {self._name}: {e}")
            yield ToolResultEvent({"toolUseId": tool_use_id, "status": "error", "content": [{"text": f"Error: {e}"}]})
            return

        if result_msg.get("type") != "tool_result" or result_msg.get("tool_call_id") != call_id:
            logger.warning(f"[WS TOOL PROXY] Unexpected message while awaiting tool_result: {result_msg}")
            yield ToolResultEvent({"toolUseId": tool_use_id, "status": "error", "content": [{"text": "Error: unexpected response from harness"}]})
            return

        content = result_msg.get("content", "")
        logger.info(f"[WS TOOL PROXY] Received tool_result for {self._name} (id={call_id})")
        yield ToolResultEvent({"toolUseId": tool_use_id, "status": "success", "content": [{"text": content}]})


def build_client_tool_wrappers(client_tools: list[dict], websocket) -> tuple[list, set]:
    """Create AgentTool instances that proxy calls over the WebSocket."""
    wrappers = []
    client_tool_names = set()

    for schema in client_tools:
        tool_name = schema["name"]
        tool_desc = schema.get("description", "")
        input_schema = schema.get("input_schema", {"type": "object", "properties": {}})
        client_tool_names.add(tool_name)
        wrappers.append(WebSocketProxyTool(tool_name, tool_desc, input_schema, websocket))

    return wrappers, client_tool_names
