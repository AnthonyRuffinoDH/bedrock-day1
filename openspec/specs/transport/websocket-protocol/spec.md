# Spec

## Purpose

Provides a bidirectional WebSocket transport layer between the Slack harness and AgentCore runtime, replacing unidirectional HTTP streaming with structured JSON message framing that supports mid-generation callbacks.

## Requirements

### Requirement: WebSocket endpoint availability
The AgentCore runtime SHALL expose a WebSocket endpoint at `/ws` alongside the existing HTTP `/invocations` endpoint, using the `@app.websocket` decorator. Both endpoints SHALL coexist — the WebSocket transport does not remove the HTTP path.

#### Scenario: WebSocket connection accepted
- **WHEN** the harness opens a WebSocket connection to the runtime's `/ws` endpoint with valid auth headers
- **THEN** the runtime SHALL accept the connection and wait for an initial JSON payload

#### Scenario: HTTP endpoint remains functional
- **WHEN** the harness sends a POST request to `/invocations` while the WebSocket endpoint is also registered
- **THEN** the HTTP endpoint SHALL process the request identically to pre-migration behavior

### Requirement: Authentication via WebSocket headers
The harness SHALL authenticate WebSocket connections using the same Cognito M2M Bearer token and session headers as the HTTP path. The `Authorization`, `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`, and `X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Id` headers SHALL be passed during the WebSocket handshake.

#### Scenario: Valid auth headers on WebSocket handshake
- **WHEN** the harness connects with a valid Bearer token and session ID header
- **THEN** the runtime SHALL accept the connection and build a `RequestContext` with the session ID populated

#### Scenario: Missing session ID
- **WHEN** the harness connects without a session ID header and sends a payload
- **THEN** the runtime SHALL send `{"type": "error", "content": "session_id is required"}` and close the connection

### Requirement: Structured JSON message framing
All messages exchanged over the WebSocket SHALL be JSON objects with a `type` field. The following message types SHALL be supported:

- `text_delta` (server to client): `{"type": "text_delta", "content": "<chunk>"}` — a partial text chunk from the LLM stream.
- `done` (server to client): `{"type": "done"}` — signals the LLM has finished generating for this turn.
- `error` (server to client): `{"type": "error", "content": "<message>"}` — signals a processing error.

#### Scenario: Text streaming over WebSocket
- **WHEN** the LLM generates text in response to a prompt sent over WebSocket
- **THEN** the runtime SHALL send one `text_delta` message per stream chunk, followed by a single `done` message when generation completes

#### Scenario: Error during processing
- **WHEN** the runtime encounters an error after accepting the connection (invalid JSON, missing session, handler failure)
- **THEN** the runtime SHALL send an `error` message with a descriptive content string and close the connection

### Requirement: Initial payload format
After the WebSocket connection is accepted, the harness SHALL send a single JSON text frame as the initial payload. This payload SHALL use the same schema as the HTTP `/invocations` body: it MUST include `prompt` and MAY include `action`, `speaker`, `channel_id`, `thread_context`, and `image_b64`.

#### Scenario: Standard invocation payload
- **WHEN** the harness sends `{"prompt": "Hello", "speaker": {...}, "channel_id": "C123", "thread_context": {...}}`
- **THEN** the runtime SHALL process it identically to the same payload sent via HTTP POST

#### Scenario: Gate evaluation payload
- **WHEN** the harness sends `{"action": "evaluate_gate", "prompt": "...", "thread_context": {...}}`
- **THEN** the runtime SHALL run the gate classification and stream the result as `text_delta` messages followed by `done`

### Requirement: Connection lifecycle
The runtime SHALL close the WebSocket connection after sending the `done` or `error` message. Each WebSocket connection handles exactly one request-response cycle (connect, send payload, receive stream, close). The harness SHALL open a new connection for each invocation.

#### Scenario: Graceful close after completion
- **WHEN** the runtime sends `{"type": "done"}`
- **THEN** the runtime SHALL close the WebSocket connection and the harness SHALL treat the buffered text as the complete response

#### Scenario: Premature disconnection
- **WHEN** the WebSocket connection drops before a `done` message is received
- **THEN** the harness SHALL treat this as a failed invocation and return an error to Slack

### Requirement: Transport selection and fallback
The harness SHALL support a `USE_WEBSOCKET` environment variable. When set to `true`, the harness SHALL attempt WebSocket transport first. If the WebSocket connection fails for any reason, the harness SHALL fall back to the HTTP streaming path and log a warning. When set to `false` (default), the harness SHALL use HTTP exclusively.

#### Scenario: WebSocket enabled and succeeds
- **WHEN** `USE_WEBSOCKET=true` and the WebSocket connection succeeds
- **THEN** the harness SHALL use the WebSocket response and not attempt HTTP

#### Scenario: WebSocket enabled but connection fails
- **WHEN** `USE_WEBSOCKET=true` and the WebSocket connection raises an exception
- **THEN** the harness SHALL log a warning and retry the same request via HTTP streaming

#### Scenario: WebSocket disabled
- **WHEN** `USE_WEBSOCKET=false` or unset
- **THEN** the harness SHALL use HTTP streaming exclusively, identical to pre-migration behavior

### Requirement: URL derivation
The harness SHALL derive the WebSocket URL from the existing `AGENT_URL` environment variable by replacing the scheme (`https` to `wss`, `http` to `ws`) and replacing the `/invocations` path suffix with `/ws`. No separate WebSocket URL configuration SHALL be required.

#### Scenario: Standard AgentCore URL conversion
- **WHEN** `AGENT_URL` is `https://bedrock-agentcore.us-west-2.amazonaws.com/runtimes/abc123/invocations`
- **THEN** the derived WebSocket URL SHALL be `wss://bedrock-agentcore.us-west-2.amazonaws.com/runtimes/abc123/ws`
