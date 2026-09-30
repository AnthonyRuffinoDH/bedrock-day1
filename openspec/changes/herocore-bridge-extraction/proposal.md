# Proposal

## Why

The WebSocket/MCP tool proxy infrastructure in `bot.py` and `main.py` is the most reusable piece of this project — it enables any Slack-connected AI agent to proxy harness-side tools to an AgentCore runtime over WebSocket. Currently it's inlined across two files (~400 lines in `bot.py`, ~80 lines in `main.py`) with no way for other teams at Delivery Hero to use it without copying the code. Extracting it into a separately installable Python library makes the proxy layer a first-class reusable primitive.

Herocore already attempted this extraction in PR #2 (`herocore/herocore-bridge-extraction` branch) but the implementation has critical bugs: auth config is not carried through the routing table (all authenticated MCP calls silently fail), missing env vars return empty headers instead of errors, and `env_file: .env` on the mcp-github container leaks all secrets. This change does the extraction correctly.

## What Changes

**New: `packages/herocore-bridge/` Python library**
- `herocore_bridge/config.py` — `load_mcp_tools()` and `build_tool_routing_table()` extracted from `bot.py:_load_mcp_tools` and the `_tool_to_server` module-level dict. The routing table carries `auth` config per-server.
- `herocore_bridge/auth.py` — `resolve_env_ref()` and `resolve_auth_headers()` extracted from `bot.py:_resolve_env_ref` and `_resolve_auth_headers`. Preserves the tuple-return `(headers, error_or_none)` contract so callers can short-circuit on missing env vars.
- `herocore_bridge/executor.py` — `execute_mcp_tool()`, stdio and HTTP execution, RPC/SSE parsing extracted from `bot.py:_execute_mcp_tool*` and `_parse_*` functions.
- `herocore_bridge/harness_client.py` — `invoke_agentcore_ws()` and `invoke_agentcore_http()` extracted from `bot.py:invoke_agentcore_ws` and `invoke_agentcore`. Uses dependency injection (`cognito_token_fn` callable, explicit `agent_url`) instead of reading module globals.
- `herocore_bridge/agent_proxy.py` — `WebSocketProxyTool` class and `build_client_tool_wrappers()` extracted from `main.py:_WebSocketProxyTool` and `_build_client_tool_wrappers`. AgentCore-side dependency (`strands-agents`) is an optional extra, not a hard requirement.
- `herocore_bridge/__init__.py` — Public API surface with lazy imports for AgentCore-side symbols.
- `pyproject.toml` — Package metadata, hatch build system, `[agentcore]` optional extras for strands-agents dependency.
- `README.md` — Installation (git subdirectory, GitHub Packages, CodeArtifact), usage examples for both harness and AgentCore sides.

**Modified: `bot.py` (harness side)**
- Remove: `_resolve_env_ref`, `_resolve_auth_headers`, `_load_mcp_tools`, `_tool_to_server`, `_execute_mcp_tool*`, `_parse_rpc_response`, `_parse_sse_response`, `_http_url_to_ws`, `invoke_agentcore_ws`, `invoke_agentcore` (~350 lines)
- Add: `from herocore_bridge import load_mcp_tools, invoke_agentcore_ws` and thin wrapper `invoke_agentcore_auto` that routes WS/HTTP and passes `cognito_token_fn`
- Keep: Slack event handlers, Cognito token function, rate limiting, thread locking, image download — all Slack-specific logic stays in `bot.py`

**Modified: `app/CustomerSupport/main.py` (AgentCore side)**
- Remove: `_WebSocketProxyTool` class, `_build_client_tool_wrappers` function, `CLIENT_NS` constant (~80 lines)
- Add: `from herocore_bridge.agent_proxy import build_client_tool_wrappers`
- Keep: Agent construction, system prompt, tool profiles, memory, gate evaluation — all agent-specific logic stays in `main.py`

**No changes to `docker-compose.yml`** — the mcp-github service does NOT get `env_file: .env` (unlike PR #2's approach). Auth continues to flow via Bearer headers from the harness.

## Capabilities

### New Capabilities

None. This is a pure refactoring extraction — no behavioral changes.

### Modified Capabilities

None. The existing specs (`transport/client-tool-proxy`, `transport/websocket-protocol`, `harness/mcp-auth`) describe behavior contracts that remain identical. The library implements the same behavior that `bot.py` and `main.py` currently implement. `skip_specs: true` is set for this change.

## Impact

- **Files modified**: `bot.py` (~350 lines removed, ~20 added), `app/CustomerSupport/main.py` (~80 lines removed, ~5 added)
- **New directory**: `packages/herocore-bridge/` with 6 Python modules, pyproject.toml, README
- **Dependencies**: `requirements.txt` adds `herocore-bridge` (local editable install during dev). The library's own deps are `requests`, `websocket-client`, `python-dotenv` (all already in requirements.txt). Optional `[agentcore]` extra adds `strands-agents` and `bedrock-agentcore`.
- **Deployment**: `agentcore deploy -y -v` required — `main.py` changes how it imports the proxy tool layer. The `Dockerfile` or `requirements.txt` bundled with the AgentCore app must install `herocore-bridge[agentcore]`.
- **Distribution**: Initially installable via `pip install -e packages/herocore-bridge` (local) or `pip install git+<repo>#subdirectory=packages/herocore-bridge` (remote). GitHub Packages PyPI feed is the medium-term target for cross-team consumption at Delivery Hero. AWS CodeArtifact is the enterprise option.
- **No breaking changes to external interfaces**: The Slack bot behavior, AgentCore WebSocket protocol, MCP tool execution, and auth header injection all remain identical.
