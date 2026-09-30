# Spec Delta

## Purpose

Enables the harness MCP proxy to authenticate with upstream MCP servers using credentials declared in configuration and resolved from environment variables at call time, so that adding a new authenticated MCP integration requires only configuration — not harness code changes.

## ADDED Requirements

### Requirement: Per-server auth configuration schema
The `mcp_tools.json` manifest SHALL support an optional `auth` object on each server entry. The `auth` object SHALL declare the authentication method the harness uses when calling that server. Omitting `auth` SHALL preserve unauthenticated behavior (no auth headers added). The harness SHALL support three auth types: `bearer`, `header`, and `basic`.

#### Scenario: Server with bearer auth
- **WHEN** a server entry contains `{"auth": {"type": "bearer", "token": "${GITHUB_TOKEN}"}}`
- **THEN** the harness SHALL recognize this as a valid bearer auth configuration

#### Scenario: Server with header auth
- **WHEN** a server entry contains `{"auth": {"type": "header", "key": "X-API-Key", "value": "${API_KEY}"}}`
- **THEN** the harness SHALL recognize this as a valid custom header auth configuration

#### Scenario: Server with basic auth
- **WHEN** a server entry contains `{"auth": {"type": "basic", "username": "${USERNAME}", "password": "${PASSWORD}"}}`
- **THEN** the harness SHALL recognize this as a valid basic auth configuration

#### Scenario: Server without auth
- **WHEN** a server entry has no `auth` field
- **THEN** the harness SHALL make HTTP requests to that server without any authentication headers, identical to current behavior

#### Scenario: Unknown auth type
- **WHEN** a server entry contains `{"auth": {"type": "oauth2", ...}}`
- **THEN** the harness SHALL log an error identifying the server and unsupported auth type, and SHALL skip authentication for that server rather than crashing

### Requirement: Environment variable secret resolution
Auth configuration values containing `${ENV_VAR}` references SHALL be resolved from the harness process environment at the time the HTTP request is constructed. The resolved values SHALL NOT be cached beyond the request, stored in configuration files, logged, or included in any agent-visible output.

#### Scenario: Token resolved from environment
- **WHEN** the auth config specifies `"token": "${GITHUB_TOKEN}"` and the environment contains `GITHUB_TOKEN=ghp_abc123`
- **THEN** the harness SHALL resolve the token to `ghp_abc123` when constructing the HTTP request

#### Scenario: Environment variable not set
- **WHEN** the auth config references `${MISSING_VAR}` and `MISSING_VAR` is not in the environment
- **THEN** the harness SHALL log an error identifying the server and missing variable, and SHALL NOT send the request (returning a tool error result instead of sending unauthenticated)

#### Scenario: Literal value without variable reference
- **WHEN** the auth config specifies `"token": "static-value"` (no `${...}` syntax)
- **THEN** the harness SHALL use the literal string as-is

### Requirement: Auth header injection for HTTP MCP calls
When making an HTTP request to an MCP server with a configured `auth` block, the harness SHALL add the appropriate HTTP header(s) to the request. For `bearer` type: `Authorization: Bearer <resolved-token>`. For `header` type: `<key>: <resolved-value>`. For `basic` type: `Authorization: Basic <base64(resolved-username:resolved-password)>`.

#### Scenario: Bearer token injected
- **WHEN** the harness calls a tool on a server configured with bearer auth and `GITHUB_TOKEN=ghp_abc123`
- **THEN** the HTTP request SHALL include the header `Authorization: Bearer ghp_abc123`

#### Scenario: Custom header injected
- **WHEN** the harness calls a tool on a server configured with `{"type": "header", "key": "X-API-Key", "value": "${API_KEY}"}` and `API_KEY=sk-test`
- **THEN** the HTTP request SHALL include the header `X-API-Key: sk-test`

#### Scenario: Basic auth injected
- **WHEN** the harness calls a tool on a server configured with basic auth, `USERNAME=admin`, and `PASSWORD=secret`
- **THEN** the HTTP request SHALL include the header `Authorization: Basic YWRtaW46c2VjcmV0`

#### Scenario: Auth does not leak into tool schemas
- **WHEN** the harness injects client tool schemas into the WebSocket payload
- **THEN** the `client_tools` array SHALL NOT contain any `auth` configuration, resolved secrets, or server URL information — only tool `name`, `description`, and `input_schema`

### Requirement: Secret non-exposure
Resolved secret values SHALL NOT appear in log output, error messages returned to the agent, tool schemas sent to the runtime, or any file written by the harness. Log messages about auth operations SHALL identify the server name and auth type but SHALL NOT include the resolved credential value.

#### Scenario: Logging omits secrets
- **WHEN** the harness resolves bearer auth for a server named "github"
- **THEN** log output SHALL contain at most `[MCP AUTH] Using bearer auth for server 'github'` — not the token value

#### Scenario: Error message omits secrets
- **WHEN** an authenticated MCP HTTP request fails with a 401 status
- **THEN** the error returned to the agent SHALL describe the failure without including the credential that was sent
