"""
herocore_bridge.agent_proxy
============================
AgentCore-side WebSocket proxy tool layer.

Extracted from: app/CustomerSupport/main.py
  _WebSocketProxyTool, _build_client_tool_wrappers

This module depends on ``strands-agents`` and ``bedrock-agentcore`` which are
*not* required by the harness side.  Install with:
    pip install "herocore-bridge[agentcore]"
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any

logger = logging.getLogger("herocore_bridge.agent_proxy")

WS_TOOL_TIMEOUT = int(os.environ.get("WS_TOOL_TIMEOUT", "30"))


class WebSocketProxyTool:
    """An AgentTool that proxies tool calls over WebSocket to the harness.

    The AgentCore runtime invokes this tool like any other Strands AgentTool.
    Instead of running locally it sends a ``tool_call`` message over the open
    WebSocket connection and waits for the matching ``tool_result`` reply from
    the harness.

    Tool names are registered under the ``client__`` namespace so they cannot
    collide with agent-side tools that share a base name.
    """

    CLIENT_NS = "client"

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict,
        websocket,
        *,
        timeout: int = WS_TOOL_TIMEOUT,
    ):
        # Import at class-definition time only when the module is actually used
        # (i.e. inside AgentCore).  This avoids ImportError on harness machines
        # that don't have strands-agents installed.
        from strands.types.tools import AgentTool  # type: ignore[import]
        # Re-attach the base class dynamically so static type checkers are happy
        # but the import is deferred.
        self.__class__.__bases__ = (AgentTool,)
        super().__init__()

        self._original_name = name
        self._name = f"{self.CLIENT_NS}__{name}"
        self._description = description
        self._input_schema = input_schema
        self._websocket = websocket
        self._timeout = timeout

    # ------------------------------------------------------------------
    # AgentTool interface
    # ------------------------------------------------------------------

    @property
    def tool_name(self) -> str:
        return self._name

    @property
    def tool_spec(self) -> dict:
        return {
            "name": self._name,
            "description": self._description,
            "inputSchema": {"json": self._input_schema},
        }

    @property
    def tool_type(self) -> str:
        return "function"

    async def stream(self, tool_use: dict, invocation_state: dict[str, Any], **kwargs: Any):
        from starlette.websockets import WebSocketDisconnect  # type: ignore[import]
        from strands.types._events import ToolResultEvent  # type: ignore[import]

        tool_use_id = tool_use.get("toolUseId", "unknown")
        tool_input = tool_use.get("input", {})
        call_id = str(uuid.uuid4())

        logger.info("[WS TOOL PROXY] Sending tool_call: %s (id=%s)", self._original_name, call_id)
        try:
            await self._websocket.send_json({
                "type": "tool_call",
                "tool_call_id": call_id,
                "name": self._original_name,
                "args": tool_input,
            })
            result_msg = await asyncio.wait_for(
                self._websocket.receive_json(),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("[WS TOOL PROXY] Timeout waiting for tool_result: %s (id=%s)", self._name, call_id)
            yield ToolResultEvent({
                "toolUseId": tool_use_id,
                "status": "error",
                "content": [{"text": f"Error: tool execution timed out after {self._timeout}s"}],
            })
            return
        except WebSocketDisconnect:
            logger.warning("[WS TOOL PROXY] Client disconnected: %s (id=%s)", self._name, call_id)
            yield ToolResultEvent({
                "toolUseId": tool_use_id,
                "status": "error",
                "content": [{"text": "Error: client disconnected during tool execution"}],
            })
            return
        except Exception as exc:  # noqa: BLE001
            logger.error("[WS TOOL PROXY] Error proxying tool %s: %s", self._name, exc)
            yield ToolResultEvent({
                "toolUseId": tool_use_id,
                "status": "error",
                "content": [{"text": f"Error: {exc}"}],
            })
            return

        if result_msg.get("type") != "tool_result" or result_msg.get("tool_call_id") != call_id:
            logger.warning("[WS TOOL PROXY] Unexpected message while awaiting tool_result: %s", result_msg)
            yield ToolResultEvent({
                "toolUseId": tool_use_id,
                "status": "error",
                "content": [{"text": "Error: unexpected response from harness"}],
            })
            return

        content = result_msg.get("content", "")
        logger.info("[WS TOOL PROXY] Received tool_result for %s (id=%s)", self._name, call_id)
        yield ToolResultEvent({
            "toolUseId": tool_use_id,
            "status": "success",
            "content": [{"text": content}],
        })


def build_client_tool_wrappers(
    client_tools: list[dict[str, Any]],
    websocket,
    *,
    timeout: int = WS_TOOL_TIMEOUT,
) -> tuple[list[WebSocketProxyTool], set[str]]:
    """Create :class:`WebSocketProxyTool` instances from a list of tool schemas.

    Parameters
    ----------
    client_tools:
        List of tool schema dicts received in the WebSocket payload under
        ``"client_tools"``.  Each dict must have ``"name"``, optionally
        ``"description"`` and ``"input_schema"``.
    websocket:
        The active Starlette ``WebSocket`` object for this request.
    timeout:
        Per-tool result wait timeout in seconds.

    Returns
    -------
    (wrappers, client_tool_names)
        ``wrappers`` — list of :class:`WebSocketProxyTool` ready to pass to
        ``Agent(tools=...)``.
        ``client_tool_names`` — set of original (non-namespaced) tool names,
        useful if the caller needs to avoid registering duplicate agent tools.
    """
    wrappers: list[WebSocketProxyTool] = []
    client_tool_names: set[str] = set()

    for schema in client_tools:
        tool_name = schema["name"]
        tool_desc = schema.get("description", "")
        input_schema = schema.get("input_schema", {"type": "object", "properties": {}})
        client_tool_names.add(tool_name)
        wrappers.append(WebSocketProxyTool(tool_name, tool_desc, input_schema, websocket, timeout=timeout))

    return wrappers, client_tool_names
