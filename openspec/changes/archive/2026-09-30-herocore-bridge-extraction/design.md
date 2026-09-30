# Design

## Context

See proposal.md for motivation. The code being extracted lives in two files:

- `bot.py` lines ~188–515: URL conversion, WebSocket/HTTP AgentCore invocation, MCP auth resolution, tool config loading, routing table, stdio/HTTP tool execution, RPC/SSE parsing
- `app/CustomerSupport/main.py` lines ~501–583: `_WebSocketProxyTool` (AgentTool subclass), `_build_client_tool_wrappers` factory

The harness side (`bot.py`) runs in Docker on the developer's machine. The AgentCore side (`main.py`) runs in the AgentCore managed container. They share no filesystem — the library must be installable independently on each side, with different dependency profiles.

Herocore's PR #2 attempted this same extraction. The design below addresses its specific failures: auth config dropped from the routing table, silent auth failures on missing env vars, secrets leaked to the GitHub MCP container, and unstable dynamic base class mutation.

## Goals / Non-Goals

**Goals:**
- Extract the WebSocket/MCP proxy layer into `packages/herocore-bridge/` as a pip-installable Python package
- Both `bot.py` and `main.py` import from the library instead of containing the implementation
- The library is separately installable via git subdirectory URL, GitHub Packages, or CodeArtifact
- Auth config flows correctly through the routing table to HTTP execution
- All existing tests and end-to-end behavior remain identical

**Non-Goals:**
- Adding new features to the proxy layer (same behavior, new packaging)
- Converting f-string logging to %-format (nice cleanup, not in scope — avoids conflating refactor noise with the extraction)
- Removing or modifying any Slack-specific logic, agent construction, system prompt, or tool profiles
- Changing `docker-compose.yml` or adding `env_file` to services that don't need it

## Decisions

### 1. Package location: `packages/herocore-bridge/`

Monorepo subdirectory under `packages/`. Pip supports installing from a git subdirectory (`pip install git+<url>#subdirectory=packages/herocore-bridge`), and hatch can build a wheel from this layout. This avoids a separate repo while keeping the library clearly separated from application code.

**Alternative**: Separate repository. Rejected — adds coordination overhead for a team of one. Can split later if cross-team usage demands it.

### 2. Build system: hatch with `pyproject.toml`

Hatch is the modern Python build backend — no `setup.py`, no `setup.cfg`. `pyproject.toml` declares metadata, dependencies, and build targets in one file. PEP 517/518 compliant.

**Alternative**: setuptools. Works but more boilerplate. Hatch is already the standard for new Python packages.

### 3. Optional extras for AgentCore-side dependencies

The library has two installation profiles:
- **Harness side** (default): `requests`, `websocket-client`, `python-dotenv` — already in `requirements.txt`
- **AgentCore side** (`[agentcore]` extra): adds `strands-agents`, `bedrock-agentcore` — only needed inside the AgentCore container

This means `pip install herocore-bridge` works on the harness without pulling in the Strands framework, and `pip install herocore-bridge[agentcore]` works in the AgentCore container.

**Alternative**: Two separate packages. Rejected — adds packaging complexity for a single shared constant (`CLIENT_NS`).

### 4. Dependency injection over module globals

The current `bot.py` uses module-level state: `_client_tools`, `_tool_to_server`, and calls `get_cognito_token()` as a module-level function. The library uses explicit parameters instead:
- `invoke_agentcore_ws(payload, ..., cognito_token_fn=..., agent_url=..., client_tools=...)` 
- `execute_mcp_tool(tool_name, args, routing_table)` 

This makes the library stateless and testable — callers own their configuration and auth.

**Alternative**: Singleton/module-level state in the library. Rejected — couples the library to specific deployment topology.

### 5. Auth config carried through the full chain

This is where PR #2 failed. The flow must be:

```
load_mcp_tools() → tool dict includes _auth from server config
                  ↓
build_tool_routing_table() → routing entry includes "auth" key
                           ↓
execute_mcp_tool() → passes server dict to _execute_mcp_tool_http()
                   ↓
_execute_mcp_tool_http() → calls resolve_auth_headers(server)
                         ↓
resolve_auth_headers() → returns (headers, error_or_none)
                       ↓
caller checks error → short-circuits with error message if env var missing
                    → merges auth headers into request if OK
```

The `_auth` key must be stored on each tool dict in `load_mcp_tools()` and included as `"auth"` in the routing table entry.

### 6. Preserve tuple-return auth contract

`resolve_auth_headers(server)` returns `(headers_dict, error_or_none)`. When `error_or_none` is not None, the caller must NOT send the request — it returns the error string to the agent instead.

PR #2 changed this to raise-and-catch internally, silently returning `{}` on missing env vars. That sends unauthenticated requests instead of failing fast. We preserve the original contract.

### 7. `WebSocketProxyTool` inherits from `AgentTool` normally

The class is defined in `agent_proxy.py` which is only imported in the AgentCore container where `strands-agents` is always installed. Normal inheritance: `class WebSocketProxyTool(AgentTool)`. The `__init__.py` uses `__getattr__` for lazy import so that importing `herocore_bridge` on the harness side doesn't trigger the `strands-agents` import.

PR #2 used `self.__class__.__bases__ = (AgentTool,)` at instance creation time — fragile, racy, and confusing to static analysis. Unnecessary when the module is guarded by lazy import.

**Alternative**: ABC/protocol-based approach without inheriting AgentTool. Rejected — Strands expects concrete AgentTool subclasses.

### 8. `header` auth field name stays `key`

The existing `mcp_tools.json` schema and `harness/mcp-auth` spec use `"key"` for the header name in `{"type": "header", "key": "X-API-Key", "value": "..."}`. The library preserves this. PR #2 renamed it to `"name"`, breaking compatibility with the spec.

### 9. Distribution roadmap

| Stage | Mechanism | When |
|---|---|---|
| Now | `pip install -e packages/herocore-bridge` (local dev) | This change |
| Now | `pip install "git+<repo>#subdirectory=packages/herocore-bridge"` (remote) | This change |
| Short-term | GitHub Packages PyPI feed (org-scoped private registry) | After first external consumer |
| Enterprise | AWS CodeArtifact domain (integrates with existing AWS infra) | When DH infra team provisions it |

The `pyproject.toml` and hatch build system produce standard wheels compatible with all three distribution channels. No code changes needed to switch — only CI/CD pipeline configuration.

### 10. No changes to `docker-compose.yml`

PR #2 added `env_file: .env` to the `mcp-github` service, leaking Cognito secrets, Slack tokens, and the GitHub PAT into the container. The GitHub MCP server in HTTP mode receives its token via Bearer header per-request — it does not need env vars. We do not change `docker-compose.yml`.

## Risks / Trade-offs

**[Risk] AgentCore container must install the library** → The `app/` directory deployed to AgentCore needs `herocore-bridge[agentcore]` in its dependency chain. If the deploy process doesn't install it, `main.py` will fail to import `build_client_tool_wrappers`. Mitigation: add it to the AgentCore app's `requirements.txt` and verify the import works after `agentcore deploy`.

**[Risk] Git subdirectory install fragility** → `pip install git+<url>#subdirectory=...` requires the full repo to be cloned. For large repos this is slow. Mitigation: acceptable for now; GitHub Packages is the medium-term fix.

**[Trade-off] Two import styles** → Harness uses `from herocore_bridge import load_mcp_tools, invoke_agentcore_ws`. AgentCore uses `from herocore_bridge.agent_proxy import build_client_tool_wrappers`. This is intentional — the lazy `__getattr__` in `__init__.py` only covers the harness-side public API to avoid importing strands-agents.

**[Trade-off] Library version not yet pinned** → Version is `0.1.0` with no release automation. Acceptable for a single-team monorepo. Needs a versioning strategy before external consumers depend on it.
