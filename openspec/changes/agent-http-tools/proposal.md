# Proposal

## Why

The agent has filesystem exploration tools but no way to fetch information from the internet, and the system prompt still frames it as a generic customer support assistant. Adding native HTTP tools lets the agent retrieve web content on demand, and a full system prompt rewrite rebrands the agent as **Herocore** — an autonomous, self-improving AI that explores its own codebase and proposes structural improvements. The goal is to get the agent to the point where it can run and grow itself: explore code, fetch documentation, analyze architecture, and propose changes — all powered by AgentCore, no raw LLM calls.

## What Changes

- **BREAKING**: Replace `SYSTEM_PROMPT` with the Herocore identity prompt. The new prompt establishes Herocore as an autonomous self-improving system, references its default agent-side tools (filesystem, HTTP, memory), describes expected client-side tools (code exploration, modification, testing via MCP), defines the EXPLORE → ANALYZE → PROPOSE operating procedure, and sets the evolutionary goal of GitHub MCP integration for turning proposals into PRs
- Add two new tool functions in `app/CustomerSupport/main.py`: `http_get(url, headers)` and `http_head(url, headers)`, using `httpx` (already in the container as a transitive dependency of `mcp`)
- Add a nested content-extraction agent pattern: raw HTTP responses are passed to a sessionless, tool-less Agent along with thread precontext, which returns distilled content to the primary agent
- Add `WEB_EXTRACTION_PROMPT` for the nested extraction agent (similar pattern to `MEMORY_AGENT_PROMPT` and `PRE_RECALL_PROMPT`)
- Configurable via environment variables: `HTTP_TOOL_TIMEOUT` (default 15s), `HTTP_MAX_RESPONSE_SIZE` (default 100KB)
- Register both tools via `_ns_tool(AGENT_NS, ...)` into a new `_http_tools` list, added to `TOOL_PROFILES["primary"]`

## Capabilities

### New Capabilities
- `agent-http-tools`: Cloud-side HTTP tools (GET, HEAD) implemented as plain functions in `main.py`, namespaced via `_ns_tool`, with a nested agent for web content extraction
- `herocore-identity`: The Herocore system prompt — defines agent identity, references default and client-side tools, establishes the EXPLORE → ANALYZE → PROPOSE operating procedure, and sets evolutionary goals

### Modified Capabilities
- `agent-tool-profiles`: The primary agent's tool set expands to include HTTP tools alongside filesystem, MCP, and memory tools

## Impact

- **`app/CustomerSupport/main.py`**: Replace `SYSTEM_PROMPT` (~30 lines → ~50 lines); add ~60 lines for HTTP tool functions, extraction agent prompt, and extraction helper; update tool profile lists
- **No new dependencies**: `httpx` is already in the container via `mcp ~= 1.24.0`; OpenTelemetry httpx instrumentation is also already present
- **No infrastructure changes**: No `agentcore.json` modifications needed
- **No harness changes**: `bot.py` is unaffected
