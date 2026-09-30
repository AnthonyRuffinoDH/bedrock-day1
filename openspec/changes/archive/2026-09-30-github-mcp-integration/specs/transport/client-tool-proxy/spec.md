# Spec Delta

## MODIFIED Requirements

### Requirement: Local MCP tool configuration
The harness SHALL read a configuration file (`mcp_tools.json`) on startup that declares available local MCP servers. Each entry SHALL specify a server name, transport type, and optionally its tool schemas and authentication configuration. The harness SHALL NOT import or contain any tool-specific logic — it acts as a generic proxy. Each server entry MAY include an `auth` object declaring the authentication method and credential references for that server's HTTP endpoint.

#### Scenario: Valid configuration loaded
- **WHEN** the harness starts and `mcp_tools.json` exists with valid server entries
- **THEN** the harness SHALL load the tool schemas and hold them ready for injection into WebSocket handshakes

#### Scenario: No configuration file
- **WHEN** the harness starts and `mcp_tools.json` does not exist
- **THEN** the harness SHALL operate normally with no client tools — WebSocket payloads SHALL omit the `client_tools` field

#### Scenario: Malformed configuration
- **WHEN** the harness starts and `mcp_tools.json` contains invalid JSON or missing required fields
- **THEN** the harness SHALL log an error and start without client tools rather than crashing

#### Scenario: Configuration with mixed auth servers
- **WHEN** the harness starts with `mcp_tools.json` containing one server with no `auth` and one server with bearer `auth`
- **THEN** the harness SHALL load tools from both servers and track the auth configuration per-server for use during tool execution

### Requirement: Decoupled tool execution
The harness SHALL proxy tool calls to local MCP server processes via stdio subprocess communication or local HTTP. The harness SHALL NOT contain imports, client libraries, or connection logic for any specific tool (e.g., no `import redis`, no GitHub API client). Tool-specific behavior is entirely encapsulated in the MCP server binaries declared in the configuration. When making HTTP calls to MCP servers, the harness SHALL apply per-server authentication as declared in the configuration.

#### Scenario: Redis tool proxied generically
- **WHEN** the LLM calls a `redis_get` tool provided by a local `@modelcontextprotocol/server-redis` MCP server
- **THEN** the harness SHALL route the call to the MCP server subprocess and return the result without any Redis-specific code in the harness

#### Scenario: Authenticated HTTP tool proxied generically
- **WHEN** the LLM calls a GitHub tool configured on an HTTP server with bearer auth
- **THEN** the harness SHALL route the call to the MCP server via HTTP with the appropriate auth header, without any GitHub-specific code in the harness
