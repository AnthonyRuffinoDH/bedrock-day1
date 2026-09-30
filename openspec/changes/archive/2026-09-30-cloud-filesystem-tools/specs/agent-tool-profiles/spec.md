# Spec Delta

## MODIFIED Requirements

### Requirement: Primary agent receives full toolset

The primary conversational agent SHALL be constructed with the complete application toolset: filesystem tools (read file, list directory, search files, get file info), MCP clients (web search, gateway), and memory tools (channel store, channel recall).

#### Scenario: Primary agent tool inventory

- **WHEN** the primary agent is constructed for a standard invocation
- **THEN** its tool list includes filesystem tools, available MCP clients, and channel memory tools

## REMOVED Requirements

### Requirement: Legacy domain tools
**Reason**: The domain-specific tools (product info, return policy) are workshop scaffolding backed by hardcoded static dictionaries with no real data. They are replaced by filesystem tools that provide genuine utility.
**Migration**: Remove `get_return_policy` and `get_product_info` from the tool set. Prompts referencing product or return policy tools will no longer work — update any dependent prompts to use filesystem tools or other available tools.
