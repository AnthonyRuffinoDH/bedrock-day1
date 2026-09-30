# Tasks

## 1. Phase 1 — WebSocket Transport Swap (POC, already implemented)

- [x] 1.1 Add `@app.websocket` handler (`ws_invoke`) to `main.py` that accepts a connection, reads a JSON payload, runs gate evaluation or standard invocation, and streams `text_delta`/`done`/`error` JSON frames. Verify by sending a payload via `generate_curl.py` adapted for WebSocket and confirming structured JSON responses.
- [x] 1.2 Add `invoke_agentcore_ws()` to `bot.py` using `websocket-client` library — derives WS URL from `AGENT_URL`, sends payload with auth/session headers, buffers `text_delta` content until `done`. Verify by setting `USE_WEBSOCKET=true` and confirming `[WS OUTBOUND]`/`[WS RESPONSE]` in harness logs.
- [x] 1.3 Add `invoke_agentcore_auto()` routing function to `bot.py` that selects WS or HTTP based on `USE_WEBSOCKET` env var and falls back to HTTP on WS failure. Wire both call sites (gate eval and main invocation) to use it. Verify by toggling `USE_WEBSOCKET` and confirming both paths produce correct Slack responses.
- [x] 1.4 Add `websocket-client>=1.6.0` to `requirements.txt` and `USE_WEBSOCKET` to `.env.example`. Verify Docker build succeeds and harness starts cleanly.
- [x] 1.5 Deploy runtime with `agentcore deploy -y -v` and rebuild harness Docker image. Verify end-to-end: send a Slack message, confirm gate evaluation and response both route through WebSocket in logs.

## 2. Phase 2 — MCP Config and Tool Discovery (harness side)

- [ ] 2.1 Create `mcp_tools.json` config schema and loader in `bot.py`. On startup, read the file if it exists; log and continue with no client tools if missing or malformed. Verify by starting the harness with a valid config and confirming tool schemas are loaded (log output), then with a missing file and confirming graceful startup.
- [ ] 2.2 Inject loaded tool schemas into the WebSocket initial payload as a `client_tools` array in `invoke_agentcore_ws()`. When no client tools are configured, omit the field. Verify by inspecting the payload logged at `[WS OUTBOUND]` with and without `mcp_tools.json`.

## 3. Phase 2 — Dynamic Tool Registration (runtime side)

- [ ] 3.1 In `ws_invoke` in `main.py`, extract `client_tools` from the initial payload. For each client tool schema, create a dynamic Strands `@tool`-decorated async function that sends `{"type": "tool_call", "tool_call_id": ..., "name": ..., "args": ...}` over the WebSocket and awaits `{"type": "tool_result", ...}` with matching `tool_call_id`. Wrap the receive with `asyncio.wait_for` (default 30s timeout). Verify by sending a payload with a fake `client_tools` entry and confirming the agent's tool list includes it.
- [ ] 3.2 Merge dynamic client tools with the existing cloud tool set (`TOOL_PROFILES["primary"]()`) when constructing the agent for the session. When `client_tools` is absent or empty, construct the agent identically to current behavior. Verify by invoking with and without `client_tools` and confirming cloud tools work in both cases.

## 4. Phase 2 — MCP Subprocess Proxy (harness side)

- [ ] 4.1 Implement a generic MCP subprocess proxy in `bot.py` that routes `tool_call` messages to the configured local MCP server process via stdio. The proxy SHALL launch the subprocess using the `command` from `mcp_tools.json`, send the tool call as MCP JSON-RPC, read the result, and return it. Verify by configuring a simple echo MCP server and confirming the round-trip works.
- [ ] 4.2 Extend the WebSocket receive loop in `invoke_agentcore_ws()` to handle `tool_call` messages. When received, route to the MCP subprocess proxy, then send `{"type": "tool_result", "tool_call_id": ..., "content": ...}` back over the WebSocket. Continue the receive loop for further `text_delta`/`tool_call`/`done` messages. Verify by triggering a client tool call from the LLM and confirming the full round-trip in harness logs.
- [ ] 4.3 Add configurable timeout for local tool execution (default 30s, via `MCP_TOOL_TIMEOUT` env var). On timeout, send a `tool_result` with error content. Verify by configuring a slow/hanging MCP server and confirming the timeout fires and returns an error to the LLM.

## 5. Phase 2 — Resilience and Error Handling

- [ ] 5.1 Handle WebSocket disconnection during a pending tool call in `bot.py`: if `recv()` raises `WebSocketConnectionClosedException` while awaiting a `tool_call` or after sending a `tool_result`, catch the exception and return an error message to Slack. Verify by killing the runtime mid-tool-call and confirming the harness posts an error to the Slack thread instead of crashing.
- [ ] 5.2 Handle client disconnect on the runtime side in `main.py`: if `websocket.receive_json()` raises `WebSocketDisconnect` while awaiting a `tool_result`, return an error string to the LLM agent so it can terminate gracefully. Verify by disconnecting the harness mid-tool-call and confirming the runtime logs the error without crashing.

## 6. Phase 2 — Deploy and Integration Verification

- [ ] 6.1 Deploy the updated runtime with `agentcore deploy -y -v`. Rebuild and restart the harness Docker image. Verify deployment succeeds and both WebSocket and HTTP endpoints respond to basic invocations (no client tools yet).
- [ ] 6.2 Configure `mcp_tools.json` with a real local MCP server (e.g., Redis), send a Slack message that triggers the LLM to use the client tool, and verify the full round-trip: harness receives `tool_call`, proxies to MCP server, returns `tool_result`, LLM incorporates the result in its response to Slack.
