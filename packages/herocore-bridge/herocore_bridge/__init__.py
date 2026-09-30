"""
herocore_bridge — public API surface.

Harness-side imports (used in bot.py):
    from herocore_bridge import load_mcp_tools, invoke_agentcore_ws, invoke_agentcore_http

AgentCore-side imports (used in main.py):
    from herocore_bridge import WebSocketProxyTool, build_client_tool_wrappers
"""

from herocore_bridge.config import load_mcp_tools
from herocore_bridge.harness_client import invoke_agentcore_ws, invoke_agentcore_http

# AgentCore-side symbols are behind a lazy import guard so the package can be
# installed in harness environments that don't have strands-agents / bedrock-agentcore.
def __getattr__(name):
    if name in ("WebSocketProxyTool", "build_client_tool_wrappers"):
        from herocore_bridge.agent_proxy import WebSocketProxyTool, build_client_tool_wrappers  # noqa: F401
        return {"WebSocketProxyTool": WebSocketProxyTool, "build_client_tool_wrappers": build_client_tool_wrappers}[name]
    raise AttributeError(f"module 'herocore_bridge' has no attribute {name!r}")


__all__ = [
    # Harness side
    "load_mcp_tools",
    "invoke_agentcore_ws",
    "invoke_agentcore_http",
    # AgentCore side (lazy)
    "WebSocketProxyTool",
    "build_client_tool_wrappers",
]
