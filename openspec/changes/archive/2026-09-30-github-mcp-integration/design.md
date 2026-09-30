# Design

## Context

The harness (`bot.py`) already implements a generic MCP tool proxy: it reads `mcp_tools.json`, injects tool schemas into WebSocket payloads, receives `tool_call` messages from the runtime, dispatches them to local MCP servers via stdio or HTTP, and returns results. The HTTP path (`_execute_mcp_tool_http`) currently makes unauthenticated requests. See proposal.md for motivation.

The filesystem MCP runs via `supergateway` wrapping the stdio-based `@modelcontextprotocol/server-filesystem` into Streamable HTTP. The GitHub MCP server (`ghcr.io/github/github-mcp-server`) has native HTTP mode and expects the GitHub PAT as a Bearer token on each incoming request — it does not consume the PAT as a server-side environment variable in HTTP mode.

Key current-state files:
- `bot.py:297-335` — `_load_mcp_tools()` and `_tool_to_server` lookup
- `bot.py:391-427` — `_execute_mcp_tool_http()` 
- `mcp_tools.json` — single filesystem server, no auth fields
- `docker-compose.yml` — harness + mcp-filesystem services
- `.env.example` — no GitHub token
- `app/CustomerSupport/main.py:44-99` — `SYSTEM_PROMPT`

## Goals / Non-Goals

**Goals:**
- Any new authenticated HTTP MCP server requires only `mcp_tools.json` + `.env` configuration
- GitHub MCP starts with `make up` and is reachable from the harness
- The agent can read repo state, create a branch, commit files, and open a PR
- Secrets never appear in logs, tool schemas, or agent-visible output

**Non-Goals:**
- Dynamic tool discovery from `tools/list` (acknowledged as future work — see Decisions)
- OAuth refresh-token flows or any auth type beyond bearer/header/basic
- PR merge, deployment, or CI/CD integration
- Changes to the AgentCore-side code beyond system prompt updates

## Decisions

### 1. Auth resolution happens at HTTP call time, not at config load time

**Decision**: `_load_mcp_tools()` stores auth config as-is (with `${VAR}` references unresolved). `_execute_mcp_tool_http()` resolves environment variables and constructs auth headers immediately before each request.

**Why**: Resolving at call time means environment changes (e.g., token rotation) take effect without restarting the harness. It also eliminates any window where resolved secrets sit in memory structures that might be serialized or logged.

**Alternative considered**: Resolve at startup and cache. Simpler but creates stale-token risk and increases the blast radius if the in-memory config is accidentally exposed.

### 2. `mcp_tools.json` auth schema uses `${ENV_VAR}` syntax with type-dispatch

The auth block uses a `type` discriminator:

```json
{"auth": {"type": "bearer", "token": "${GITHUB_TOKEN}"}}
{"auth": {"type": "header", "key": "X-API-Key", "value": "${API_KEY}"}}
{"auth": {"type": "basic", "username": "${USER}", "password": "${PASS}"}}
```

**Why**: Three types cover the realistic auth patterns for HTTP MCP servers without speculative complexity. The `${VAR}` syntax is familiar, unambiguous, and simple to implement with regex substitution. The type discriminator makes header construction explicit rather than requiring the harness to guess the auth scheme.

**Alternative considered**: A generic `headers` dict where the user writes raw header key/value pairs. More flexible but loses semantic validation (e.g., can't warn "missing token for bearer auth") and makes it harder to enforce secret non-exposure.

### 3. Environment variable resolution implementation

A helper function `_resolve_env_ref(value: str) -> str | None` handles the substitution:
- If `value` matches `${...}`, extract the variable name and look it up in `os.environ`
- If the variable is not set, return `None` (caller decides how to handle — for auth, this means returning a tool error)
- If `value` does not contain `${...}`, return it as-is (literal)

This function is called per-field when constructing auth headers. It is not applied to non-auth config fields — only auth-related values undergo substitution.

### 4. Auth header construction in `_execute_mcp_tool_http`

The existing function gains a pre-request step:

```python
def _resolve_auth_headers(server: dict) -> tuple[dict, str | None]:
    """Build auth headers from server config. Returns (headers, error_or_none)."""
```

This reads `server["auth"]`, resolves references, and returns a headers dict. On failure (missing env var, unknown type), it returns an error string. The caller adds these headers to the existing `Content-Type` / `Accept` headers. No auth → empty dict, no change to existing behavior.

### 5. GitHub MCP server as a native HTTP Docker service

**Decision**: Run `ghcr.io/github/github-mcp-server` directly with `--http` flag. No `supergateway` wrapper needed — unlike the filesystem MCP, the GitHub server has native HTTP support.

**Docker Compose entry**:
```yaml
mcp-github:
  image: ghcr.io/github/github-mcp-server
  command: ["--http", "--port", "8082"]
  restart: unless-stopped
```

The service is internal to the Compose network. The harness addresses it at `http://mcp-github:8082/mcp`. No host port publication.

**Why**: The GitHub MCP server's HTTP mode accepts the PAT as an incoming `Authorization: Bearer` header on each request. This aligns perfectly with the harness auth architecture — the harness injects the token from `${GITHUB_TOKEN}` per-request. No server-side `GITHUB_PERSONAL_ACCESS_TOKEN` env var is needed in HTTP mode.

**Note**: The exact command-line flags and MCP endpoint path need to be verified against the actual image. The design assumes `--http --port 8082` based on the GitHub MCP documentation, but implementation should verify by inspecting the image's help output or README.

### 6. Tool schemas are hand-maintained in `mcp_tools.json` (for now)

**Decision**: GitHub tool schemas are manually declared in `mcp_tools.json`, matching the canonical names and input schemas from the GitHub MCP server's `tools/list`.

**Why**: Dynamic discovery would require the harness to call `tools/list` on each MCP server at startup, parse the response, and merge the results with any locally-declared schemas. This is a meaningful architectural change (startup ordering, error handling, schema caching) that is orthogonal to the auth and GitHub integration goals.

**Trade-off acknowledged**: Adding or updating GitHub MCP tools requires manually editing `mcp_tools.json`. If the upstream server changes tool schemas, our declarations become stale. This is the same pattern already used for filesystem tools.

**Future direction**: A `make sync-tools` target or harness startup step that calls `tools/list` on each configured server and updates `mcp_tools.json` automatically. This would move schema ownership to the MCP servers themselves.

### 7. Tool surface selection

Expose 8 tools from the GitHub MCP server:

| Tool | Purpose |
|------|---------|
| `get_file_contents` | Read files and directories from repo |
| `search_code` | Search for code patterns |
| `list_branches` | List repository branches |
| `create_branch` | Create a new branch |
| `create_or_update_file` | Commit a file change |
| `list_commits` | View commit history |
| `create_pull_request` | Open a PR |
| `get_pull_request` | Read PR details |

Explicitly excluded: `merge_pull_request`, `delete_branch`, `create_release`, `update_repository`, and admin/CI tools. The agent cannot merge its own work.

### 8. System prompt update strategy

**Decision**: Add a focused `<github_tools>` section to the existing `SYSTEM_PROMPT` in `main.py` that:
- Explains GitHub tools are client-side (prefixed `client__`), provided by the harness
- Distinguishes them from agent-side filesystem tools (read-only, local) and agent-side tools
- Describes the self-improvement workflow: explore → branch → commit → PR → stop
- Explicitly states the boundary: no merge, no deploy

**Why**: The tool schemas themselves provide parameter documentation. The prompt only needs to convey workflow guidance and boundaries — not duplicate schema details.

### 9. `_tool_to_server` carries auth config

The existing `_tool_to_server` dict (built at module load from `_load_mcp_tools()`) already stores per-tool server metadata (`name`, `command`, `transport`, `url`). It gains an `auth` key carrying the raw auth config dict (with unresolved `${VAR}` references). This is passed to `_execute_mcp_tool_http` which resolves it at call time.

### 10. Makefile setup integration

The `setup` target gains a post-AWS step that checks for `GITHUB_TOKEN`:

```makefile
@if grep -q '^GITHUB_TOKEN=$$' .env 2>/dev/null || ! grep -q '^GITHUB_TOKEN=' .env 2>/dev/null; then \
    if [ -n "$$GITHUB_TOKEN" ]; then \
        sed -i "s|^GITHUB_TOKEN=.*|GITHUB_TOKEN=$$GITHUB_TOKEN|" .env; \
        echo "  GITHUB_TOKEN populated from environment"; \
    else \
        echo "  ⚠ GITHUB_TOKEN not set. GitHub MCP will not work until configured in .env"; \
    fi; \
fi
```

This follows the existing pattern where `setup-cognito` auto-populates from AWS and tells the user what remains manual.

## Risks / Trade-offs

**[Risk] GitHub MCP HTTP mode command flags may differ from documentation** → Mitigation: Implementation task includes verifying the actual image behavior by running `docker run --rm ghcr.io/github/github-mcp-server --help` before finalizing the Compose entry and endpoint path.

**[Risk] Hand-maintained tool schemas drift from upstream** → Mitigation: Schemas are verified against `tools/list` during implementation. Future `make sync-tools` target planned but out of scope. The risk is manageable because the tool surface is small (8 tools) and the GitHub MCP server is versioned.

**[Risk] GitHub PAT scope creep** → Mitigation: Document minimum scopes in `.env.example`. For the target use case (read repo + create branch + commit + open PR on repos the token owner has access to), the minimum is `repo` scope for private repos or `public_repo` for public-only.

**[Risk] Missing GITHUB_TOKEN causes silent failures** → Mitigation: `_resolve_env_ref` returns `None` for missing vars, and `_resolve_auth_headers` converts this to an explicit error returned to the agent (not a silent unauthenticated request). The `make setup` flow also warns about missing tokens.

**[Trade-off] No dynamic tool discovery** — We accept manual schema maintenance for simplicity now. The 8-tool surface is small enough that this is not a significant burden, but it would not scale to dozens of tools across many MCP servers.

**[Trade-off] No health check for mcp-github in Compose** — The GitHub MCP server does not expose a health endpoint in HTTP mode. The harness will get connection errors if the service isn't ready, which are returned to the agent as tool errors. Adding a startup delay or retry would add complexity without much benefit since tool calls are user-triggered, not startup-critical.
