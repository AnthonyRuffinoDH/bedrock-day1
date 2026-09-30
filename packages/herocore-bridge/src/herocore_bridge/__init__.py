"""herocore-bridge: WebSocket/MCP tool proxy bridge for AgentCore."""

_HARNESS_API = {
    "load_mcp_tools": "config",
    "build_tool_routing_table": "config",
    "invoke_agentcore_ws": "harness_client",
    "invoke_agentcore_http": "harness_client",
    "http_url_to_ws": "harness_client",
    "execute_mcp_tool": "executor",
    "resolve_auth_headers": "auth",
    "resolve_env_ref": "auth",
}


def __getattr__(name):
    module_name = _HARNESS_API.get(name)
    if module_name is not None:
        import importlib
        mod = importlib.import_module(f".{module_name}", __name__)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
