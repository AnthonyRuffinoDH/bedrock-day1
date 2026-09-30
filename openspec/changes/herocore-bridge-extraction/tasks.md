# Tasks

## 1. Package scaffolding

- [ ] 1.1 Create `packages/herocore-bridge/` directory with `pyproject.toml` (hatch build system, version 0.1.0, core deps: `requests>=2.31.0`, `websocket-client>=1.6.0`, `python-dotenv>=1.0.0`, optional `[agentcore]` extra for `strands-agents` and `bedrock-agentcore`). Verify: `pip install -e packages/herocore-bridge` succeeds and `python -c "import herocore_bridge"` exits 0
- [ ] 1.2 Create `packages/herocore-bridge/src/herocore_bridge/__init__.py` with lazy `__getattr__` that exposes the harness-side public API (`load_mcp_tools`, `build_tool_routing_table`, `invoke_agentcore_ws`, `invoke_agentcore_http`, `execute_mcp_tool`, `resolve_auth_headers`, `resolve_env_ref`, `http_url_to_ws`) without importing `strands-agents`. Verify: `python -c "from herocore_bridge import load_mcp_tools"` succeeds; `python -c "from herocore_bridge import build_client_tool_wrappers"` raises ImportError when strands-agents is not installed

## 2. Auth and config modules

- [ ] 2.1 Create `packages/herocore-bridge/src/herocore_bridge/auth.py` — extract `_resolve_env_ref()` and `_resolve_auth_headers()` from `bot.py:300-343`. Public names: `resolve_env_ref(value) -> str | None`, `resolve_auth_headers(server) -> tuple[dict, str | None]`. Preserve the `_ENV_REF_PATTERN` regex and all three auth types (bearer, header with `key` field name, basic). Verify: unit test covers bearer with valid env var, bearer with missing env var returning error string, header auth, basic auth, and no-auth passthrough
- [ ] 2.2 Create `packages/herocore-bridge/src/herocore_bridge/config.py` — extract `_load_mcp_tools()` from `bot.py:350-377` as `load_mcp_tools(config_path) -> list[dict]` (explicit path parameter, no module-level call). Extract routing table construction from `bot.py:382-390` as `build_tool_routing_table(tools) -> dict[str, dict]` where each entry carries `name`, `command`, `transport`, `url`, and `auth` keys. Verify: unit test loads a sample `mcp_tools.json` with two servers (one with auth, one without), confirms routing table carries auth for the authenticated server and `None` for the other

## 3. MCP executor module

- [ ] 3.1 Create `packages/herocore-bridge/src/herocore_bridge/executor.py` — extract `_execute_mcp_tool()`, `_execute_mcp_tool_stdio()`, `_execute_mcp_tool_http()`, `_parse_rpc_response()`, `_parse_sse_response()` from `bot.py:393-513`. Public API: `execute_mcp_tool(tool_name, args, routing_table, timeout=30) -> str`. Internal functions stay private. The HTTP executor must call `resolve_auth_headers()` from `auth.py` and short-circuit on error. Verify: unit test mocks an HTTP MCP server, calls `execute_mcp_tool` with bearer auth configured, and confirms the Authorization header is sent; a second test with a missing env var confirms the error string is returned without making an HTTP request

## 4. Harness client module

- [ ] 4.1 Create `packages/herocore-bridge/src/herocore_bridge/harness_client.py` — extract `invoke_agentcore` from `bot.py:142-185` as `invoke_agentcore_http(payload, agent_url, cognito_token_fn, session_id, user_id=None) -> str`. Uses dependency injection for `cognito_token_fn` (callable returning token string) and explicit `agent_url` instead of `os.getenv`. Verify: unit test mocks `requests.post` and confirms correct headers including session ID and optional user ID
- [ ] 4.2 In the same module, extract `_http_url_to_ws` from `bot.py:188-200` as `http_url_to_ws(url) -> str` and `invoke_agentcore_ws` from `bot.py:203-289` as `invoke_agentcore_ws(payload, agent_url, cognito_token_fn, session_id, user_id=None, client_tools=None, tool_executor=None) -> str`. The `tool_executor` parameter defaults to `execute_mcp_tool` from `executor.py`. The WebSocket loop injects `client_tools` schemas into the payload and dispatches `tool_call` messages to `tool_executor`. Verify: unit test mocks websocket connection, sends a text_delta then done message, and confirms the response is returned; a second test sends a tool_call, confirms `tool_executor` is invoked, and the tool_result is sent back

## 5. AgentCore proxy module

- [ ] 5.1 Create `packages/herocore-bridge/src/herocore_bridge/agent_proxy.py` — extract `_WebSocketProxyTool` class from `main.py:504-568` as `WebSocketProxyTool(AgentTool)` and `_build_client_tool_wrappers` from `main.py:571-583` as `build_client_tool_wrappers(client_tools, websocket) -> tuple[list, set]`. Export `CLIENT_NS = "client"` as a module constant shared between harness and AgentCore sides. This module imports `strands-agents` at module level (guarded by the lazy `__getattr__` in `__init__.py`). Verify: `python -c "from herocore_bridge.agent_proxy import WebSocketProxyTool, build_client_tool_wrappers, CLIENT_NS"` succeeds in the AgentCore container (or when strands-agents is installed)

## 6. Rewire bot.py (harness side)

- [ ] 6.1 Remove extracted functions from `bot.py`: `_resolve_env_ref`, `_resolve_auth_headers`, `_ENV_REF_PATTERN`, `_load_mcp_tools`, `_tool_to_server` construction, `_execute_mcp_tool`, `_execute_mcp_tool_stdio`, `_execute_mcp_tool_http`, `_parse_rpc_response`, `_parse_sse_response`, `_http_url_to_ws`, `invoke_agentcore`, `invoke_agentcore_ws` (~350 lines removed). Add imports: `from herocore_bridge import load_mcp_tools, build_tool_routing_table, invoke_agentcore_ws as _bridge_invoke_ws, invoke_agentcore_http as _bridge_invoke_http, execute_mcp_tool`. Replace module-level `_client_tools = _load_mcp_tools()` with `_client_tools = load_mcp_tools(...)` and `_tool_to_server = build_tool_routing_table(_client_tools)`. Update `invoke_agentcore_auto` to call the bridge functions with `cognito_token_fn=get_cognito_token` and `agent_url=os.getenv("AGENT_URL")`. Verify: `python -c "import bot"` succeeds with no import errors; `python bot.py` starts and connects to Slack (manual check)
- [ ] 6.2 Update `requirements.txt` to add `herocore-bridge` as an editable local install reference (or add a comment noting the dependency). Verify: `pip install -r requirements.txt` succeeds

## 7. Rewire main.py (AgentCore side)

- [ ] 7.1 Remove `_WebSocketProxyTool` class, `_build_client_tool_wrappers` function, and `CLIENT_NS` constant from `main.py` (~80 lines removed). Add import: `from herocore_bridge.agent_proxy import build_client_tool_wrappers, CLIENT_NS`. The `ws_invoke` handler continues to call `build_client_tool_wrappers(client_tools, websocket)` exactly as before. Verify: `python -c "from CustomerSupport.main import app"` succeeds with no import errors (requires `herocore-bridge[agentcore]` installed)
- [ ] 7.2 Add `herocore-bridge[agentcore]` to the AgentCore app's dependency chain (in `app/` requirements or equivalent). Verify: after adding the dependency, the import from 7.1 succeeds. Note: `agentcore deploy -y -v` is required after this change (~3 min deploy)

## 8. Integration verification

- [ ] 8.1 Run `python -c "from herocore_bridge import load_mcp_tools, invoke_agentcore_ws; from herocore_bridge.agent_proxy import build_client_tool_wrappers"` to confirm both import paths work. Then run the full test suite (if any) and verify no regressions. Verify: all imports succeed, no test failures
- [ ] 8.2 Test end-to-end via `generate_curl.py` or direct WebSocket invocation against the deployed AgentCore runtime — send a prompt that triggers a client tool call (e.g., a GitHub MCP tool) and confirm the tool proxying round-trip completes successfully. Verify: the agent responds with tool results, not tool proxy errors
