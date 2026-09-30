# Proposal

## Why

The current architecture uses unidirectional HTTP streaming (`requests.post` with `stream=True` in `bot.py` → `@app.entrypoint` async generator in `main.py`). This constrains all communication to a single request-response cycle: the harness sends a prompt and passively consumes text chunks until the stream ends. There is no mechanism for the cloud agent to call back to the harness mid-generation — which means tools that require local infrastructure behind the harness's firewall (databases, local MCP servers, proprietary services) cannot be exposed to the LLM without deploying them into the AgentCore environment.

Switching to a persistent, bidirectional WebSocket connection removes this constraint. The harness can receive tool-call requests from the cloud agent, execute them against local MCP servers, and return results — all within a single conversational turn. This transforms `bot.py` from a passive consumer into an active MCP proxy without coupling it to any specific tool implementation.

## What Changes

- **BREAKING**: Replace the HTTP streaming protocol between `bot.py` and `main.py` with a WebSocket-based bidirectional protocol. The `invoke_agentcore()` function in `bot.py` and the `@app.entrypoint` decorator in `main.py` both change signatures.
- **BREAKING**: Change `agentcore.json` runtime protocol from `"HTTP"` to `"WEBSOCKET"` (or equivalent), requiring a redeployment.
- Add structured JSON framing to the WebSocket stream (`text_delta`, `done`, `tool_call`, `tool_result` message types) replacing the current raw `data: ` SSE-style lines.
- Add a local MCP tool configuration system (`mcp_tools.json`) to `bot.py` that declares available local MCP servers without importing their implementation.
- Add client-tool schema injection into the WebSocket handshake (`start_turn` with `client_tools` array) so the cloud agent dynamically discovers harness-side tools.
- Add tool-call routing in `main.py` that distinguishes cloud tools (executed locally in the AgentCore container) from client tools (proxied back over the WebSocket to the harness).
- Add generic MCP subprocess proxy in `bot.py` that routes tool calls to local MCP server binaries via stdio/HTTP without containing tool-specific logic.

## Capabilities

### New Capabilities
- `transport/websocket-protocol`: Defines the bidirectional WebSocket transport layer between the Slack harness and AgentCore runtime, including connection lifecycle, structured JSON message framing (`text_delta`, `done`, `tool_call`, `tool_result`, `start_turn`), authentication handshake, and graceful disconnection/error handling.
- `transport/client-tool-proxy`: Defines how the harness discovers local MCP tool schemas, injects them into the cloud agent's tool configuration via the WebSocket handshake, intercepts tool-call messages, proxies execution to local MCP server processes, and returns results — all without the harness containing tool-specific logic.

### Modified Capabilities
- (none — existing specs `agent-tool-profiles`, `explicit-agent-memory`, and `slack-conversation-context` describe agent-level behavior that is transport-independent; the WebSocket migration changes the transport layer beneath them without altering their requirements)

## Impact

- **`bot.py`**: `invoke_agentcore()` (line 139) rewritten from HTTP `requests.post` streaming to WebSocket client. New MCP config loader and subprocess proxy added. New dependency on a WebSocket client library (`websockets` or `websocket-client`).
- **`main.py`**: `@app.entrypoint` decorator (line 227) replaced with WebSocket equivalent. `invoke()` function restructured to accept/send structured JSON frames and handle bidirectional tool routing. Gate evaluation path also migrated.
- **`agentcore/agentcore.json`**: Runtime `protocol` field changes from `"HTTP"` to WebSocket equivalent. Requires `agentcore validate` and `agentcore deploy -y -v`.
- **New file**: `mcp_tools.json` (or similar) — local MCP server configuration for the harness.
- **Dependencies**: `bot.py` gains a WebSocket client library. `main.py` may need WebSocket support from `bedrock_agentcore.runtime` (verify `BedrockAgentCoreApp` WebSocket decorator availability).
- **Phasing**: Phase 1 (transport swap) must be deployed and verified before Phase 2 (tool proxying) begins. Both phases require AgentCore redeployment.
