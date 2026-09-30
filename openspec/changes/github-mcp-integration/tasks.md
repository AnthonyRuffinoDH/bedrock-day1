# Tasks

## 1. Harness Auth Infrastructure

- [ ] 1.1 Add `_resolve_env_ref(value: str) -> str | None` helper to `bot.py` that extracts `${VAR}` references and resolves them from `os.environ`, returning `None` for missing vars and the literal string when no `${...}` pattern is present. Verify: unit-test-style check that `_resolve_env_ref("${HOME}")` returns a value, `_resolve_env_ref("${NONEXISTENT_VAR_XYZ}")` returns `None`, and `_resolve_env_ref("literal")` returns `"literal"`.

- [ ] 1.2 Add `_resolve_auth_headers(server: dict) -> tuple[dict, str | None]` helper to `bot.py` that reads `server["auth"]`, calls `_resolve_env_ref` per field, and returns `(headers_dict, error_or_none)`. Support `bearer` (→ `Authorization: Bearer <token>`), `header` (→ `<key>: <value>`), and `basic` (→ `Authorization: Basic <b64>`). No `auth` key → `({}, None)`. Unknown type → log warning, return `({}, None)`. Missing env var → return `({}, "Error: ...")`. Verify: call with each auth type using test env vars and confirm correct headers; call with missing var and confirm error string returned.

- [ ] 1.3 Update `_execute_mcp_tool_http` in `bot.py` to call `_resolve_auth_headers(server)` before making the HTTP request. If an error is returned, short-circuit with the error message (do not send the request). Otherwise merge the auth headers into the existing `Content-Type`/`Accept` headers. Verify: set a test env var, configure a server entry with bearer auth pointing at a local HTTP echo or the filesystem MCP, and confirm the `Authorization` header appears in the request.

- [ ] 1.4 Update `_load_mcp_tools` to read and store the `auth` config from each server entry. Update the `_tool_to_server` lookup to carry the `auth` dict (unresolved). Verify: load a `mcp_tools.json` with one auth and one no-auth server; confirm `_tool_to_server` entries have correct `auth` values. Confirm existing filesystem tools still work (no auth → no change in behavior).

- [ ] 1.5 Verify that `_load_mcp_tools` strips `auth`, `_server`, `_command`, `_transport`, and `_url` metadata from tool dicts before they are injected into the WebSocket `client_tools` payload — only `name`, `description`, and `input_schema` should be sent to the runtime. Verify: inspect the `client_tools` array in the WebSocket payload and confirm no auth or URL data is present.

- [ ] 1.6 Verify secret non-exposure: confirm that log messages from `_resolve_auth_headers` include server name and auth type but never the resolved token value. Confirm that error messages returned to the agent on 401 or missing env var do not contain credential values.

## 2. GitHub MCP Docker Service

- [ ] 2.1 Verify the GitHub MCP server's HTTP mode by running `docker pull ghcr.io/github/github-mcp-server` and `docker run --rm ghcr.io/github/github-mcp-server --help` to confirm the exact command-line flags for HTTP mode, port configuration, and MCP endpoint path. Document findings for subsequent tasks.

- [ ] 2.2 Add the `mcp-github` service to `docker-compose.yml`: use `ghcr.io/github/github-mcp-server` with the HTTP command flags confirmed in 2.1, no published ports, `restart: unless-stopped`. Add `mcp-github` to the harness `depends_on` list alongside `mcp-filesystem`. Verify: `docker compose config` parses without errors.

- [ ] 2.3 Run `make up` and confirm both `mcp-filesystem` and `mcp-github` services start. Verify `mcp-github` is reachable from the harness container via Docker DNS (e.g., `docker compose exec harness curl -s http://mcp-github:<port>/mcp` returns an MCP response or connection-accepted indicator, not a connection-refused error).

## 3. GitHub Tool Schemas and Configuration

- [ ] 3.1 Query the running GitHub MCP server's `tools/list` endpoint to get the canonical tool names and input schemas for the 8 target tools: `get_file_contents`, `search_code`, `list_branches`, `create_branch`, `create_or_update_file`, `list_commits`, `create_pull_request`, `get_pull_request`. Verify: the response includes all 8 tools. Record exact names and schemas — these may differ from documentation.

- [ ] 3.2 Update `mcp_tools.json` to add the `github` server entry with `transport: "http"`, the confirmed URL, bearer auth referencing `${GITHUB_TOKEN}`, and tool declarations matching the canonical schemas from 3.1. Preserve the existing `filesystem` server entry unchanged. Verify: the harness loads the updated config without errors and reports the correct total tool count across both servers.

- [ ] 3.3 Confirm the harness can successfully execute a read-only GitHub tool call (e.g., `list_branches` or `get_file_contents`) through the full MCP proxy path with `GITHUB_TOKEN` set. Verify: the tool returns actual repository data, not an auth error.

## 4. Credential Setup and Documentation

- [ ] 4.1 Add `GITHUB_TOKEN=` to `.env.example` with comments documenting: purpose (GitHub MCP access for Herocore), minimum required scopes (`repo` for private repos, `public_repo` for public-only), and where to create the token. Verify: `.env.example` contains the entry and comments are accurate.

- [ ] 4.2 Add a `setup-github` target to the Makefile that checks whether `GITHUB_TOKEN` is set in `.env` and either auto-populates it from the shell environment or prints a warning. Add `setup-github` to the `setup` dependency chain after `setup-agent-url`. Verify: running `make setup` with `GITHUB_TOKEN` unset prints the warning; running with it set populates `.env`.

## 5. Remote Agent System Prompt

- [ ] 5.1 Add a `<github_tools>` section to `SYSTEM_PROMPT` in `app/CustomerSupport/main.py` that: (a) explains GitHub tools are client-side (`client__` prefixed), provided by the harness; (b) distinguishes them from agent-side filesystem tools (read-only, local workspace) and other agent-side tools; (c) describes the self-improvement workflow (explore → read authoritative code from GitHub → create branch → commit changes → open PR → stop); (d) explicitly prohibits merging PRs or triggering deployments. Verify: the prompt text is present in the `SYSTEM_PROMPT` string. Requires `agentcore deploy -y -v` to take effect on the runtime.

- [ ] 5.2 Update the `<future_context>` section at the end of `SYSTEM_PROMPT` to reflect that GitHub MCP integration is now implemented (not just planned). Verify: the text no longer refers to GitHub integration as a future goal.

## 6. Integration Validation

- [ ] 6.1 With the full stack running (`make up`), invoke the agent via WebSocket (or `generate_curl.py`) with a prompt asking it to list branches of the repository. Confirm the agent uses the `client__list_branches` tool, the harness authenticates to GitHub MCP, and the agent returns branch information. This validates the full path: agent → runtime → harness → authenticated MCP HTTP → GitHub MCP → GitHub API.

- [ ] 6.2 Perform an end-to-end self-improvement test: prompt the agent to create a branch, make a trivial file change (e.g., add a timestamp to a test file), and open a PR. Confirm the agent creates the branch via `client__create_branch`, commits via `client__create_or_update_file`, opens a PR via `client__create_pull_request`, returns the PR URL, and stops without attempting to merge. Verify: the PR exists on GitHub with the expected branch, commit, and description.
