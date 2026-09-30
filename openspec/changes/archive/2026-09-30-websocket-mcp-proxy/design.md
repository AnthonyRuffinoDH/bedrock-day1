# Design

## Context

See proposal.md for motivation. The key finding from the POC (already implemented and deployed) is that `BedrockAgentCoreApp` already exposes a `/ws` WebSocket endpoint via `@app.websocket` alongside the HTTP `/invocations` endpoint — both routes are registered by the Starlette-based framework on the same server. No `agentcore.json` protocol change or infrastructure redeployment is needed for the transport itself. The POC proved the Phase 1 transport swap works end-to-end in production (gate evaluation + standard invocation over WebSocket).

Current transport state after POC:
- `main.py` has both `@app.entrypoint` (HTTP) and `@app.websocket` (WS) handlers
- `bot.py` has `invoke_agentcore_auto()` routing to WS or HTTP based on `USE_WEBSOCKET` env var, with automatic HTTP fallback
- `websocket-client` library added to harness `requirements.txt`
- Structured JSON framing (`text_delta`/`done`/`error`) implemented and working

What remains: Phase 2 — the dynamic MCP tool proxying layer that extends the working WebSocket protocol with `tool_call`/`tool_result`/`start_turn` message types.

## Goals / Non-Goals

**Goals:**
- Extend the proven WebSocket protocol with bidirectional tool-call routing
- Enable the cloud LLM to invoke tools running behind the harness's firewall
- Keep the harness generic — no tool-specific imports or logic in `bot.py`
- Maintain full HTTP fallback (without tool proxying) for resilience

**Non-Goals:**
- Real-time streaming of partial responses to Slack (the harness still buffers until `done`)
- Concurrent tool execution — tool calls are handled sequentially within a turn
- Securing the Gateway endpoint with JWT (tracked separately per CLAUDE.md)
- Replacing the existing cloud-side MCP clients (Exa, Gateway) — those remain server-side

## Decisions

### 1. One-connection-per-turn model (not persistent)

Each Slack message opens a fresh WebSocket connection, sends one payload, streams one response, and closes. This matches the existing HTTP request-per-message model and avoids connection pooling, reconnection logic, and stale-auth complexity.

**Why not persistent connections:** Cognito M2M tokens have limited TTL. The harness processes messages sequentially per thread (thread lock). The overhead of a fresh WS handshake (~50ms measured in POC) is negligible compared to LLM generation time (~6-15s). Persistent connections would add complexity for no user-visible benefit.

### 2. `websocket-client` (synchronous) over `websockets` (asyncio)

The harness (`bot.py`) runs on `slack_bolt` which is synchronous (threading model, not asyncio). Using `websocket-client`'s synchronous `create_connection` avoids an asyncio bridge. The blocking `recv()` loop is fine because the thread lock already serializes per-thread work.

**Alternative considered:** `websockets` (asyncio) — would require `asyncio.run()` wrappers inside Bolt's sync event handlers, adding complexity for no benefit.

### 3. Client tools as Strands `@tool`-decorated wrappers on main.py

On the runtime side, client tools from `client_tools` will be registered as dynamically-created Strands `@tool` functions. Each wrapper sends a `tool_call` JSON frame down the WebSocket, blocks on `receive` for the `tool_result`, and returns the content string. This lets Strands handle tool dispatch uniformly — it doesn't know the difference between a cloud tool and a proxied client tool.

**Why this approach:** The Strands Agent framework manages tool calling internally. Rather than intercepting the framework's tool dispatch, we give it tool functions that happen to proxy over WebSocket. The agent is constructed fresh per-session anyway (`get_or_create_agent`), so adding dynamic tools at construction time is natural.

**Alternative considered:** Intercepting the Strands stream loop to detect client-tool calls and handle them externally. Rejected because it couples to Strands internals and would break if the framework changes its streaming contract.

### 4. MCP tool discovery via static config file (`mcp_tools.json`)

The harness reads `mcp_tools.json` at startup. Each entry declares an MCP server's name, launch command, and tool schemas. On each WebSocket invocation, the harness injects these schemas into the `client_tools` field. Tool execution routes to the declared MCP server subprocess.

**Why static config over dynamic discovery:** Dynamic discovery (querying each MCP server for its schema at startup) would require launching all server processes eagerly and adds a failure mode. Static config is simpler, deterministic, and sufficient for the initial deployment. Dynamic discovery can be added later.

**Config format:**
```json
{
  "servers": [
    {
      "name": "redis",
      "command": ["npx", "@modelcontextprotocol/server-redis", "redis://localhost:6379"],
      "transport": "stdio",
      "tools": [
        {
          "name": "redis_get",
          "description": "Get a value from Redis by key",
          "input_schema": { "type": "object", "properties": { "key": { "type": "string" } }, "required": ["key"] }
        }
      ]
    }
  ]
}
```

### 5. WebSocket handler needs to be async-aware for bidirectional messaging

The current POC `ws_invoke` is a simple send-then-receive-loop. For tool proxying, the handler must interleave: stream LLM output, detect when Strands calls a client tool (the wrapper blocks on `websocket.receive`), and relay the result back. Since Strands `stream_async` is an async generator and the tool wrappers need to `await websocket.receive_json()`, this naturally fits the async model already in use.

The key change is that the `@app.websocket` handler will construct client-tool wrappers that capture the `websocket` object and use it for bidirectional communication mid-stream.

### 6. No `agentcore.json` changes required

The `/ws` WebSocket route is always registered by `BedrockAgentCoreApp` as part of the `"HTTP"` protocol. Confirmed in the SDK source (`app.py` line 198: `WebSocketRoute("/ws", self._handle_websocket)`). No protocol field change, no infra redeployment needed for the transport layer.

## Risks / Trade-offs

**[Strands tool wrapper blocking during WS receive]** → The dynamically-created tool wrappers will `await websocket.receive_json()` to get the tool result. If the harness disconnects or hangs, this blocks the agent indefinitely. **Mitigation:** Add an `asyncio.wait_for` timeout (configurable, default 30s) around the receive call. On timeout, return an error string to the LLM so it can handle gracefully.

**[MCP subprocess lifecycle management]** → Launching MCP server subprocesses per-tool-call (stdio transport) adds latency. **Mitigation:** For the initial implementation, accept the latency. Future optimization: keep long-running MCP server processes warm and route via local HTTP.

**[No HTTP fallback for tool-proxied calls]** → When `USE_WEBSOCKET=true` and the harness injects `client_tools`, those tools only work over WebSocket. If fallback to HTTP occurs, the cloud agent won't have client tools available. **Mitigation:** This is acceptable — HTTP fallback preserves core functionality (cloud tools, memory, gate). Client tools are additive. Log clearly when fallback strips client tools.

**[32KB WebSocket frame limit]** → AgentCore enforces a 32KB max frame size per the docs. Large tool results (e.g., a big Redis value) could exceed this. **Mitigation:** Document the limit. For the initial implementation, truncate oversized tool results with an error note. Chunked tool results are a future enhancement.

## Migration Plan

1. **Phase 1 (complete):** WebSocket transport swap deployed and verified. HTTP fallback operational. `USE_WEBSOCKET=true` set in production `.env`.
2. **Phase 2 implementation:** Add `tool_call`/`tool_result`/`start_turn` message handling to both `main.py` and `bot.py`. Add `mcp_tools.json` config and MCP subprocess proxy to the harness.
3. **Phase 2 deploy:** `agentcore deploy -y -v` for the runtime changes. Rebuild harness Docker image with updated `bot.py`. No infrastructure changes needed.
4. **Rollback:** Set `USE_WEBSOCKET=false` in `.env` and restart the harness container. All traffic reverts to HTTP. No AgentCore redeployment needed.
