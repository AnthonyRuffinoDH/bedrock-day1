# Spec Delta

## MODIFIED Requirements

### Requirement: Primary agent receives full toolset

The primary conversational agent SHALL be constructed with the complete application toolset: filesystem tools (read file, list directory, search files, get file info), HTTP tools (GET, HEAD), MCP clients (web search, gateway), and memory tools (channel store, channel recall).

#### Scenario: Primary agent tool inventory

- **WHEN** the primary agent is constructed for a standard invocation
- **THEN** its tool list includes filesystem tools, HTTP tools, available MCP clients, and channel memory tools
