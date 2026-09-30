# Proposal

## Why

The hardcoded `get_return_policy` and `get_product_info` tools in `main.py` are workshop scaffolding — static dictionaries that demonstrate tool use but provide no real value. Meanwhile, the harness-side MCP filesystem tools (proxied over WebSocket) proved immediately useful for exploring the codebase. Adding equivalent filesystem tools directly on the AgentCore runtime side gives the agent native file access without the WebSocket round-trip, and removes the dead workshop code.

## What Changes

- **BREAKING**: Remove `get_return_policy` tool, `RETURN_POLICIES` dict, `get_product_info` tool, and `PRODUCTS` dict from `main.py`
- Add four new cloud-side `@tool`-decorated functions in `main.py`: `read_file`, `list_directory`, `search_files`, `get_file_info` — scoped to a configurable allowed directory (default: the container's working directory)
- Update `TOOL_PROFILES["primary"]` to replace domain tools with the new filesystem tools
- Update `SYSTEM_PROMPT` to reference filesystem capabilities instead of product/return policy support

## Capabilities

### New Capabilities
- `cloud-filesystem-tools`: Cloud-side filesystem exploration tools (read, list, search, info) implemented as native Strands `@tool` functions in `main.py`, scoped to a configurable allowed directory

### Modified Capabilities
- `agent-tool-profiles`: The primary agent's tool set changes — domain tools (product info, return policy) are replaced by filesystem tools. The requirement describing the primary agent's toolset needs to reflect the new tool composition.

## Impact

- **`app/CustomerSupport/main.py`**: Remove ~40 lines of hardcoded data and two tool functions; add ~60 lines of filesystem tool implementations; update `_domain_tools` list and `SYSTEM_PROMPT`
- **No infrastructure changes**: No `agentcore.json` modifications, no new dependencies, no deploy needed beyond redeploying the runtime code
- **No harness changes**: `bot.py` and `mcp_tools.json` are unaffected — the harness-side MCP filesystem tools continue to work independently
- **Backwards compatibility**: Removing the product/return-policy tools is breaking for any prompts that reference them, but these are workshop-only tools with no real data
