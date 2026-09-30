# Design

## Context

The agent currently runs the old customer-support system prompt with filesystem tools, MCP clients, and memory tools. See proposal.md for why we're adding HTTP tools and rebranding to Herocore. The existing patterns — `_ns_tool` for namespacing, nested agents for memory, `_resolve_safe_path` for filesystem security — establish the conventions this design follows.

The container already has `httpx` (transitive dep of `mcp ~= 1.24.0`) and its OpenTelemetry instrumentation. No new dependencies are needed.

## Goals / Non-Goals

**Goals:**
- Add GET and HEAD HTTP tools following the established `_ns_tool` namespacing pattern
- Extract useful content from raw HTTP responses via a nested sessionless agent
- Replace the system prompt with the Herocore identity, referencing all tool categories
- Keep everything powered by AgentCore — no raw LLM calls

**Non-Goals:**
- POST/PUT/DELETE or any write-capable HTTP methods
- URL allowlisting or domain filtering (wide open by design)
- Replacing the Exa MCP web search (that continues independently)
- Modifying bot.py or the harness in any way

## Decisions

### 1. Use `httpx` for HTTP requests

**Choice:** `httpx.Client` (sync) for GET and HEAD.
**Rationale:** Already in the container via `mcp`. Has OpenTelemetry instrumentation pre-wired. Cleaner API than `urllib.request`. Sync is fine — the tool functions are synchronous like the filesystem tools.
**Alternative:** `urllib.request` (stdlib) — works but no OTel integration, verbose error handling. `requests` — also in container transitively but `httpx` is the more modern choice and a direct `mcp` dependency.

### 2. Nested content-extraction agent for GET responses

**Choice:** Pass raw response body + thread precontext to a sessionless, tool-less Agent that returns distilled content. Same pattern as `pre_recall_channel_memory`.
**Rationale:** Raw HTML/JSON dumped into the primary agent's context wastes tokens and confuses the model. A dedicated extraction agent with the thread context can pick out only the relevant information. Using AgentCore (via `load_model()`) keeps everything on-platform — no raw LLM calls.
**Alternative:** Return raw text and let the primary agent handle it — simple but wastes context and degrades quality on large pages.

### 3. `WEB_EXTRACTION_PROMPT` as a module-level constant

**Choice:** A constant string prompt, similar to `MEMORY_AGENT_PROMPT` and `PRE_RECALL_PROMPT`.
**Rationale:** Consistent with existing patterns. The prompt instructs the extraction agent to receive raw web content and thread precontext, and return only the information relevant to the current conversation.

### 4. Configurable timeout and size via environment variables

**Choice:** `HTTP_TOOL_TIMEOUT` (default 15s) and `HTTP_MAX_RESPONSE_SIZE` (default 102400 bytes / 100KB), read at module level like `_FS_ALLOWED_DIR` and `WS_TOOL_TIMEOUT`.
**Rationale:** Operators can tune without code changes. Defaults are conservative — 15s prevents hanging on slow servers, 100KB covers most web pages while limiting context bloat.

### 5. HEAD returns raw headers, GET returns extracted content

**Choice:** `http_head` returns status code + headers as formatted text (no extraction agent). `http_get` fetches body, truncates if needed, passes to extraction agent with thread precontext, returns extracted content.
**Rationale:** HEAD responses are small metadata — no extraction needed. GET responses need extraction because raw HTML is noisy. The extraction agent receives the thread's recent messages as precontext so it knows what information is relevant.

### 6. Tool placement and registration

**Choice:** Define `http_get` and `http_head` as plain functions after the filesystem tools. Create `_http_tools` list via `_ns_tool(AGENT_NS, ...)`. Add to `TOOL_PROFILES["primary"]`.
**Rationale:** Follows the exact pattern established by `_filesystem_tools`. The tools register as `agent__http_get` and `agent__http_head`.

### 7. Herocore system prompt replaces SYSTEM_PROMPT

**Choice:** Replace the entire `SYSTEM_PROMPT` constant. The new prompt has sections for identity/directive, default agent-side tools (filesystem, HTTP, memory), expected client-side tools (MCP code tools), the EXPLORE→ANALYZE→PROPOSE operating procedure, memory tools/reconciliation (preserved from current prompt), and evolutionary context (GitHub MCP goal).
**Rationale:** The memory tool guidance and reconciliation rules are battle-tested and must be preserved. The rest is a rewrite to establish the Herocore identity and self-improvement directive. The prompt references tool namespaces (agent-side vs client-side) so the model understands the tool landscape.

## Risks / Trade-offs

**[Response size]** Large web pages could still be large after truncation to 100KB, consuming extraction agent tokens.
**Mitigation:** The 100KB cap is configurable. The extraction agent distills to only relevant content, so the primary agent receives a compact summary regardless.

**[Extraction quality]** The nested agent may miss relevant content or include noise.
**Mitigation:** Thread precontext gives the extraction agent enough signal to focus. This is the same pattern used successfully for memory pre-recall.

**[No SSRF protection]** Wide-open URL access means the agent could hit internal endpoints.
**Mitigation:** Accepted for now — the container's network environment is controlled by AgentCore. A future change can add IP filtering if needed.

**[Breaking prompt change]** The Herocore prompt fundamentally changes agent behavior.
**Mitigation:** This is intentional — the goal is to pivot from generic support to autonomous self-improvement. Memory tool guidance is preserved to avoid regression.
