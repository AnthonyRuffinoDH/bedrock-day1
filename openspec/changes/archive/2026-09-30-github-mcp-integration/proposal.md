# Proposal

## Why

Herocore can explore its own codebase (via cloud filesystem tools and local filesystem MCP) and propose changes, but has no way to act on those proposals. Adding a GitHub MCP integration through the existing harness-side tool proxy completes the first self-improvement loop: the agent can inspect code, create a branch, commit changes, and open a pull request — with a human still reviewing and merging. This is the minimum viable write path that keeps the agent useful without making it autonomous.

## What Changes

- **Evolve `mcp_tools.json` into an integration manifest**: The configuration gains per-server `auth` blocks that declare how the harness should authenticate when calling each MCP server. Authentication types (`bearer`, `header`, `basic`) are generic — not GitHub-specific. Secret values use `${ENV_VAR}` references resolved from the harness environment at call time. No `auth` block preserves today's unauthenticated behavior.

- **Generalize harness MCP HTTP authentication**: `bot.py`'s `_execute_mcp_tool_http` function gains a pre-request step that reads the server's `auth` configuration, resolves environment references, and attaches the appropriate HTTP headers. This is the only harness code change needed — the rest of the MCP flow (JSON-RPC, SSE parsing, tool routing) remains unchanged.

- **Add GitHub MCP server to Docker Compose**: The official `ghcr.io/github/github-mcp-server` image runs in HTTP mode alongside the existing filesystem MCP server. It is internal to the Compose network (no host port exposure). The harness addresses it via Docker DNS at `http://mcp-github:8082/mcp`. The GitHub MCP server expects the PAT as an incoming Bearer token on each request, which aligns with the generalized auth architecture — the harness injects `Authorization: Bearer ${GITHUB_TOKEN}` from its environment.

- **Expose focused GitHub tool surface**: A curated set of GitHub MCP tools is declared in `mcp_tools.json` — enough for repository inspection and the branch→commit→PR workflow: `get_file_contents`, `search_code`, `list_branches`, `create_branch`, `create_or_update_file`, `list_commits`, `create_pull_request`, `get_pull_request`. Schemas match the canonical GitHub MCP server output.

- **GitHub credential setup**: `GITHUB_TOKEN` is added to `.env.example`. The `make setup` flow detects and reports the credential requirement. Minimum required GitHub token scopes are documented.

- **Update remote agent guidance**: The system prompt in `main.py` gains a brief section distinguishing GitHub repository operations (client-side, write-capable, goes through PRs) from filesystem exploration (read-only, local workspace) and agent-side tools. The prompt provides workflow guidance (explore → branch → change → PR → stop) without duplicating tool schemas.

- **Tool schema ownership strategy**: GitHub tool schemas in `mcp_tools.json` are initially hand-maintained to match the deployed GitHub MCP server version. The proposal acknowledges this as a maintenance debt and calls out dynamic discovery from `tools/list` as a future improvement, but does not implement it in this change.

## Capabilities

### New Capabilities
- `harness/mcp-auth`: Generic per-server HTTP authentication in the harness MCP proxy. Covers the `auth` configuration schema in `mcp_tools.json`, environment-variable secret resolution, and header injection for `bearer`, `header`, and `basic` auth types.
- `github-mcp-tools`: GitHub repository operations exposed as client-side MCP tools through the harness. Covers the tool surface (read, branch, commit, PR), Docker Compose integration, credential setup, and the agent's understanding of the GitHub workflow boundary (PR creation is the terminal action — no merge, no deploy).

### Modified Capabilities
- `transport/client-tool-proxy`: The harness proxy now resolves per-server authentication when making HTTP MCP calls. The core proxy contract (config-driven, no tool-specific logic, timeout enforcement) is unchanged, but the HTTP transport path gains auth header injection.

## Impact

- **`bot.py`**: `_load_mcp_tools` reads `auth` config per server; `_execute_mcp_tool_http` resolves secrets and attaches auth headers. `_tool_to_server` carries auth metadata.
- **`mcp_tools.json`**: Structure changes from flat server list to manifest with auth blocks. Existing filesystem server entry gains no `auth` (preserving current behavior). GitHub server entry added.
- **`docker-compose.yml`**: New `mcp-github` service; harness gains `depends_on` for it.
- **`.env.example`**: New `GITHUB_TOKEN` variable documented with scope guidance.
- **`Makefile`**: `setup` target updated to detect/report GitHub token requirement.
- **`app/CustomerSupport/main.py`**: `SYSTEM_PROMPT` updated with GitHub tool guidance and self-improvement workflow instructions.
- **`Dockerfile.mcp-filesystem`**: No changes — filesystem MCP is unaffected.
- **Dependencies**: No new Python packages. The GitHub MCP server is a pre-built Docker image.
- **Security boundary**: The GitHub PAT never appears in `mcp_tools.json`, logs, prompts, or agent-visible output. It is resolved from the environment only at HTTP call time in the harness process.
