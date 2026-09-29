# Tasks

## 1. Slack conversation context helpers in bot.py

- [x] 1.1 Add `get_user_info(user_id)` function that calls `app.client.users_info(user=user_id)` and returns `{"user_id": ..., "display_name": ..., "real_name": ...}`. On failure, return a fallback dict with just the raw user_id. Add an in-memory `_user_info_cache` dict to avoid repeated lookups. Verify: call the function with a known Slack user ID via a short test script and confirm it returns display_name and real_name; call with an invalid ID and confirm fallback.

- [x] 1.2 Add `get_thread_context(channel_id, thread_ts)` function that calls `app.client.conversations_replies(channel=channel_id, ts=thread_ts, limit=THREAD_CONTEXT_LIMIT)` where `THREAD_CONTEXT_LIMIT` defaults to 10 (configurable via env var). Returns a dict with `bot_has_participated`, `sender_is_sole_human`, `participant_count`, and `recent_messages` (each message as `{"user": ..., "display_name": ..., "text": ..., "ts": ...}`). For a top-level message with no thread, return defaults: `bot_has_participated=False`, `sender_is_sole_human=True`, `recent_messages=[]`. Verify: call the function in a thread with known bot replies and confirm `bot_has_participated=True`; call on a top-level message and confirm defaults.

- [x] 1.3 Integrate `get_user_info` and `get_thread_context` into `handle_message`. After resolving `user_id`, `channel_id`, and `thread_ts`, call both helpers and build the `speaker` and `thread` context dicts. Verify: add a log statement after building context and confirm both dicts are populated when a Slack message arrives.

## 2. Help-biased gate evaluation

- [x] 2.1 Update the gate evaluation call in `bot.py` `handle_message` to include thread context in the payload: `{"action": "evaluate_gate", "prompt": text, "thread_context": {...}}`. Verify: inspect the log output of `invoke_agentcore` for a gate call and confirm the `thread_context` key is present.

- [x] 2.2 Rewrite the `evaluate_gate` handler in `main.py` to extract `thread_context` from the payload and build a gate prompt that includes thread signals (bot_has_participated, sender_is_sole_human, recent messages). The prompt must instruct the classifier to default to `RESPOND` for ambiguous messages and treat `bot_has_participated=True` as a strong signal to respond. Verify: invoke via `generate_curl.py` with `{"action": "evaluate_gate", "prompt": "thanks", "thread_context": {"bot_has_participated": true, "sender_is_sole_human": true, ...}}` and confirm the gate returns `RESPOND`; invoke with `{"action": "evaluate_gate", "prompt": "hey are you coming to lunch?", "thread_context": {"bot_has_participated": false, ...}}` and confirm `IGNORE`.

- [x] 2.3 Remove the thumbs-up reaction on `IGNORE` in `bot.py`. When the gate returns `IGNORE`: call `unreact(channel_id, ts, "thinking_face")` and return immediately — no `react(channel_id, ts, "+1")`. Verify: trigger an `IGNORE` gate decision and confirm no thumbs-up reaction appears on the message; only the thinking-face is removed.

## 3. Decouple memory from normal agent invocation

- [x] 3.1 In `main.py` `get_or_create_agent`, remove the call to `get_memory_session_manager(session_id, user_id)` and pass `session_manager=None` to the `Agent` constructor. The function signature changes to `get_or_create_agent(session_id, channel_id)` — `user_id` is no longer needed for agent construction. Verify: invoke the agent via `generate_curl.py` with a standard prompt and confirm it responds without errors; confirm no memory-related log lines from the session manager appear.

- [x] 3.2 Remove the `X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Id` header from `invoke_agentcore` in `bot.py` for standard (non-gate) invocations. The header may remain for gate evaluations (harmless). Verify: inspect the outbound HTTP headers logged by `invoke_agentcore` for a standard invocation and confirm the custom-user-id header is absent.

- [x] 3.3 Update the normal invocation payload in `bot.py` to include `speaker`, `channel_id`, and `thread_context` alongside `prompt` and optional `image_b64`. Verify: inspect the payload logged by `invoke_agentcore` for a standard call and confirm all context fields are present.

- [x] 3.4 Update the standard invocation handler in `main.py` to extract `speaker`, `channel_id`, and `thread_context` from the payload and prepend them to the prompt as a structured context block (so the agent sees who is speaking and the recent thread). Verify: invoke via `generate_curl.py` with a payload containing speaker and thread_context and confirm the agent's response reflects awareness of the speaker's name.

## 4. Explicit channel memory tools

- [x] 4.1 Add `TOOL_PROFILES` dict to `main.py` with `"primary"` and `"memory"` profiles. `"primary"` returns the full tool list (domain tools + MCP clients + memory tools, once defined). `"memory"` returns an empty list. Update `get_or_create_agent` to use `TOOL_PROFILES["primary"]()` when constructing agents. Verify: confirm the primary agent is constructed with the same tools as before (domain + MCP), plus placeholders for memory tools.

- [x] 4.2 Add a request-scoped context dict `_request_context` (keyed by session_id) in `main.py`. In the `invoke` entrypoint, store `channel_id` and `session_id` in `_request_context[session_id]` before calling `get_or_create_agent`. Verify: add a log statement confirming `_request_context` is populated for each invocation.

- [x] 4.3 Create `get_channel_memory_session_manager(channel_id, session_id)` in `memory/session.py` that constructs an `AgentCoreMemorySessionManager` with `actor_id=channel_id` and `session_id=session_id`, using the same `MEMORY_ID` and `REGION` as the existing factory. Use retrieval config with namespace `/users/{channel_id}/facts` (top_k=3, relevance_score=0.3) and `/summaries/{channel_id}/{session_id}` (top_k=3, relevance_score=0.3). Verify: call the function and confirm it returns a valid session manager instance (or None if MEMORY_ID is unset).

- [x] 4.4 Implement `channel_memory_store(content: str) -> str` as a `@tool` in `main.py`. It reads `channel_id` and `session_id` from `_request_context`, constructs a secondary `Agent` with `session_manager=get_channel_memory_session_manager(channel_id, session_id)`, `tools=TOOL_PROFILES["memory"]()`, and a `MEMORY_AGENT_PROMPT` system prompt. It sends the content as a synthetic message and returns a confirmation string. On failure, return a graceful error message. Verify: invoke the main agent via `generate_curl.py` and manually trigger the tool (requires `agentcore deploy` — flag this). After deploy, confirm the memory store completes without error and the content appears in subsequent recall.

- [x] 4.5 Implement `channel_memory_recall(query: str) -> str` as a `@tool` in `main.py`. Same secondary agent construction as store. Sends the query and returns the agent's response (which will be hydrated with memory context by the session manager). On failure, return a message indicating no memory was found. Verify: after storing content via 4.4, invoke recall with a related query and confirm relevant content is returned.

- [x] 4.6 Add `channel_memory_store` and `channel_memory_recall` to the `"primary"` tool profile in `TOOL_PROFILES`. Verify: confirm the primary agent's tool list includes both memory tools.

## 5. System prompt update

- [x] 5.1 Replace the `<memory_architecture_and_rules>` block in `SYSTEM_PROMPT` with new guidance that: explains memory is available via `channel_memory_store` and `channel_memory_recall` tools; instructs the agent to use thread context from the payload as primary conversational state; directs the agent to recall memory only when prior channel knowledge would materially help; directs the agent to store memory only for durably useful information; treats memory results as supporting context not authoritative. Verify: read the updated `SYSTEM_PROMPT` and confirm it contains no references to automatic memory injection, session IDs, or the old memory architecture rules.

## 6. Integration verification

- [x] 6.1 Run `agentcore validate` to confirm the configuration is still valid after all code changes. Verify: command exits 0 with no errors.

- [x] 6.2 Run `agentcore deploy -y -v` to deploy the updated agent. Verify: deployment completes successfully. (Note: deploy takes ~3 minutes.)

- [ ] 6.3 End-to-end test via Slack: send an untagged ambient message that is a plausible support question in a channel where the bot is present. Confirm the bot responds (help-biased gate). Send a clearly social message in a thread where the bot has not participated. Confirm the bot stays silent (no reaction, no reply). In a thread where the bot has replied, send a follow-up question and confirm the bot responds with awareness of the thread context.

- [ ] 6.4 End-to-end memory test via Slack: in a conversation, provide information the agent should find durably useful (e.g., "our team's preferred return policy is always full refund"). Confirm the agent stores it via channel memory. In a new thread in the same channel, ask a question where that stored knowledge would help. Confirm the agent recalls it and uses it in its response.
