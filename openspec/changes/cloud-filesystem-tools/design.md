# Design

## Context

The agent currently carries two `@tool`-decorated functions (`get_return_policy`, `get_product_info`) backed by hardcoded dictionaries. These are workshop scaffolding with no real data. The harness side already has MCP-based filesystem tools (proxied over WebSocket), proving the pattern useful. This design adds equivalent tools natively in main.py so the cloud agent can explore files without a WebSocket round-trip. See proposal.md for full motivation.

The `@tool` decorator works well for simple synchronous functions (unlike the WebSocket proxy case that needed `AgentTool`). The existing Strands `@tool` pattern in main.py (`channel_memory_store`, `channel_memory_recall`) is the model to follow.

## Goals / Non-Goals

**Goals:**
- Replace workshop scaffolding with genuinely useful filesystem tools
- Secure all file access behind a configurable allowed-directory boundary
- Keep the implementation simple: plain `@tool` functions, no new dependencies

**Non-Goals:**
- Write access (all tools are read-only)
- Replacing the harness-side MCP filesystem tools (those continue independently)
- Recursive file content reading or archive extraction
- Large-file handling (streaming, pagination) — files are returned in full

## Decisions

### 1. Use `@tool` decorator, not `AgentTool` subclass

**Choice:** Standard `@tool`-decorated functions.
**Rationale:** These are simple synchronous functions with well-defined parameters that Pydantic can introspect cleanly. The `AgentTool` subclass was needed for WebSocket proxy tools because their parameters (websocket, dynamic name) confused the decorator's Pydantic model builder. No such issue here.
**Alternative:** `AgentTool` subclass — unnecessary complexity for straightforward file operations.

### 2. Path security via `os.path.realpath` prefix check

**Choice:** Every tool resolves the requested path with `os.path.realpath()` (which resolves symlinks and `..` traversal), then verifies the result starts with the resolved allowed directory using `os.path.commonpath`.
**Rationale:** This is the standard Python approach for path containment. It handles symlink escapes, `../` traversal, and relative paths in one check.
**Alternative:** `pathlib.Path.resolve()` — equivalent, but `os.path` is already used elsewhere and the check is a one-liner.

### 3. Shared validation helper

**Choice:** A single `_resolve_safe_path(requested_path) -> (resolved_path, error_string | None)` helper used by all four tools.
**Rationale:** Centralizes the security boundary. Each tool calls it first and returns the error string immediately if validation fails.

### 4. Allowed directory via `FS_ALLOWED_DIR` environment variable

**Choice:** `FS_ALLOWED_DIR` env var, defaulting to `os.getcwd()`.
**Rationale:** Matches the container's working directory by default (which is `/app` in the Dockerfile). Operators can override to scope access to a specific data directory without code changes.
**Alternative:** Hardcoded `/app` — less flexible, breaks local dev.

### 5. Tool placement in main.py

**Choice:** Define the four `@tool` functions and the helper immediately after the memory tool definitions (replacing the `RETURN_POLICIES`, `PRODUCTS`, `get_return_policy`, `get_product_info` block). Update `_domain_tools` to reference the new tools.
**Rationale:** Keeps tool definitions grouped. The `_domain_tools` list becomes `_filesystem_tools` to reflect the new composition.

### 6. SYSTEM_PROMPT update

**Choice:** Replace the `<support_guidelines>` section's tool references. Remove mentions of product/return-policy tools. Add a note that the agent can explore files within its allowed directory.
**Rationale:** The system prompt should reflect actual capabilities. No other sections need changes — memory tools, conversational context, and reconciliation remain as-is.

## Risks / Trade-offs

**[File size]** `read_file` returns entire file contents. A very large file could inflate the LLM context window and increase cost/latency.
**Mitigation:** Acceptable for now — the agent operates on a small application directory. A future enhancement could add optional line-range parameters.

**[Glob performance]** `search_files` uses `pathlib.Path.rglob()` which walks the full tree.
**Mitigation:** The container filesystem is small. The configurable allowed directory naturally limits scope.

**[No write access]** The agent cannot create or modify files.
**Mitigation:** This is intentional (non-goal). Write access would require a separate security review.
