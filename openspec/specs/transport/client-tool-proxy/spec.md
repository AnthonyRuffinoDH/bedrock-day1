# Spec

## Purpose

Enables the Slack harness to act as a generic MCP tool proxy, allowing the cloud LLM to dynamically discover and invoke tools running behind the harness's firewall without the harness containing tool-specific logic.

## Requirements

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

### Requirement: Client tool schema injection
When opening a WebSocket connection, the harness SHALL include a `client_tools` array in the initial payload. Each entry SHALL be a JSON Schema-compatible tool definition containing `name`, `description`, and `input_schema`. The runtime SHALL merge these with its own cloud tools when constructing the agent for the request.

#### Scenario: Tools injected into handshake
- **WHEN** the harness sends the initial WebSocket payload with `client_tools` containing two tool schemas
- **THEN** the runtime SHALL construct the agent with its cloud tools plus the two client tools available for LLM selection

#### Scenario: No client tools in payload
- **WHEN** the harness sends the initial WebSocket payload without a `client_tools` field
- **THEN** the runtime SHALL construct the agent with cloud tools only, identical to current behavior

### Requirement: Tool call routing
When the LLM selects a tool during generation, the runtime SHALL determine whether the tool is a cloud tool or a client tool. Cloud tools SHALL be executed locally in the AgentCore container. Client tools SHALL be proxied to the harness over the WebSocket.

#### Scenario: Cloud tool selected
- **WHEN** the LLM calls `get_product_info` (a cloud-defined tool)
- **THEN** the runtime SHALL execute it locally and feed the result back to the LLM without any WebSocket messages to the harness

#### Scenario: Client tool selected
- **WHEN** the LLM calls a tool whose name matches a `client_tools` entry
- **THEN** the runtime SHALL send `{"type": "tool_call", "tool_call_id": "<id>", "name": "<tool>", "args": {...}}` to the harness and suspend LLM generation until the result arrives

### Requirement: Tool result protocol
When the harness receives a `tool_call` message, it SHALL execute the tool against the appropriate local MCP server and send `{"type": "tool_result", "tool_call_id": "<id>", "content": "<result>"}` back over the WebSocket. The runtime SHALL inject this result into the LLM context and resume generation.

#### Scenario: Successful tool execution round-trip
- **WHEN** the runtime sends a `tool_call` and the harness returns a `tool_result` with the same `tool_call_id`
- **THEN** the runtime SHALL provide the result content to the LLM and resume text generation

#### Scenario: Tool execution failure
- **WHEN** the local MCP server returns an error or the subprocess fails
- **THEN** the harness SHALL send a `tool_result` with `content` describing the error, allowing the LLM to handle the failure gracefully

### Requirement: Decoupled tool execution
The harness SHALL proxy tool calls to local MCP server processes via stdio subprocess communication or local HTTP. The harness SHALL NOT contain imports, client libraries, or connection logic for any specific tool (e.g., no `import redis`, no GitHub API client). Tool-specific behavior is entirely encapsulated in the MCP server binaries declared in the configuration. When making HTTP calls to MCP servers, the harness SHALL apply per-server authentication as declared in the configuration.

#### Scenario: Redis tool proxied generically
- **WHEN** the LLM calls a `redis_get` tool provided by a local `@modelcontextprotocol/server-redis` MCP server
- **THEN** the harness SHALL route the call to the MCP server subprocess and return the result without any Redis-specific code in the harness

#### Scenario: Authenticated HTTP tool proxied generically
- **WHEN** the LLM calls a GitHub tool configured on an HTTP server with bearer auth
- **THEN** the harness SHALL route the call to the MCP server via HTTP with the appropriate auth header, without any GitHub-specific code in the harness

### Requirement: Tool execution timeout
The harness SHALL enforce a configurable timeout on local tool execution. If a tool call exceeds the timeout, the harness SHALL return a `tool_result` indicating a timeout error rather than hanging indefinitely.

#### Scenario: Tool exceeds timeout
- **WHEN** a local MCP server takes longer than the configured timeout to respond
- **THEN** the harness SHALL send `{"type": "tool_result", "tool_call_id": "<id>", "content": "Tool execution timed out"}` and the LLM SHALL receive this as the tool's output

### Requirement: Connection resilience during tool proxying
The WebSocket connection SHALL remain open during tool call round-trips. If the connection drops during a pending tool call, the harness SHALL return an error message to Slack. The runtime SHALL treat a missing tool result (client disconnect) as a tool failure and terminate the generation with an error.

#### Scenario: Connection lost during tool call
- **WHEN** the WebSocket connection drops while the harness is executing a local tool
- **THEN** the harness SHALL detect the disconnect and post an error message to the Slack thread

#### Scenario: Harness sends result after timeout on runtime side
- **WHEN** the runtime's internal timeout for a client tool result expires before the harness responds
- **THEN** the runtime SHALL treat the tool as failed, send an `error` message, and close the connection
