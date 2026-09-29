# Design

## Context

See proposal.md for motivation. The key constraints shaping this design:

- **AgentCore Memory namespace templates** in `agentcore.json:43-49` use `{actorId}` and `{sessionId}` as template variables (`/users/{actorId}/facts`, `/summaries/{actorId}/{sessionId}`). These are populated by the `actor_id` and `session_id` passed to `AgentCoreMemoryConfig`. The templates themselves are not changing — we change *when* and *how* they are populated.
- **Strands Agent `session_manager`** auto-couples memory to every agent turn. Passing `session_manager=None` disables this entirely. There is no partial or selective mode.
- **No direct Bedrock API access** — model calls go through AgentCore Runtime. Secondary agents for memory operations use the same `load_model()` path.
- **Slack `conversations.replies`** returns up to 1000 messages per call. We only need recent context, so a single call with a small `limit` is sufficient.

## Goals / Non-Goals

**Goals:**

- The harness gathers enough Slack context to make informed gate decisions and supplies it to the agent.
- Normal agent invocations have no automatic memory identity or session manager.
- Channel memory is accessible only through explicit tool calls that construct isolated secondary agents.
- Tool profiles are configuration-driven, with recursion prevention for nested agents.

**Non-Goals:**

- User-level memory (user+channel scoping) — explicitly excluded per project decision; only channel-scoped memory is supported.
- Changing AgentCore Memory infrastructure (`agentcore.json` memory strategies and namespace templates remain as-is).
- Securing the Gateway with JWT (tracked separately).
- Changing the Cognito auth flow or session ID derivation.

## Decisions

### 1. Thread context gathering in bot.py

**Decision:** Add two helper functions to `bot.py`: `get_user_info(user_id)` calling `users.info`, and `get_thread_context(channel_id, thread_ts)` calling `conversations.replies`.

**Why:** The harness already has `app.client` (a Slack `WebClient`). These are the minimal Slack API calls needed to satisfy the spec requirements. No new dependencies.

**Structure of thread context:**

```python
{
    "speaker": {"user_id": "U123", "display_name": "alice", "real_name": "Alice Smith"},
    "thread": {
        "bot_has_participated": True,
        "sender_is_sole_human": False,
        "participant_count": 3,
        "recent_messages": [
            {"user": "U123", "display_name": "alice", "text": "...", "ts": "..."},
            ...
        ]
    }
}
```

`recent_messages` is capped by a `THREAD_CONTEXT_LIMIT` env var (default: 10 messages). This keeps payloads small while giving the gate and agent enough conversational context.

**Alternative considered:** Fetching full thread history and summarizing. Rejected — adds latency and complexity; the agent already handles long context well, and 10 recent messages is sufficient for routing and continuity.

### 2. Help-biased gate prompt with thread signals

**Decision:** Rewrite the gate evaluator prompt in `main.py` to accept a structured context block (thread participation, participant list, recent messages) alongside the raw message. The prompt explicitly instructs the classifier to default to `RESPOND` for ambiguous messages and to use `bot_has_participated` as a strong signal.

**Why:** The current gate prompt (`main.py:146-154`) sees only the raw message text with no thread context. It cannot distinguish "a question in a thread where the bot is active" from "random chatter in an unrelated thread."

**Gate payload changes:**

```python
{
    "action": "evaluate_gate",
    "prompt": "<raw message text>",
    "thread_context": { ... }  # same structure as above
}
```

**Alternative considered:** Moving gate logic into bot.py as a heuristic (e.g., always respond if bot has participated). Rejected — the LLM gate can handle nuanced cases (e.g., "thanks" in a bot-active thread is a social acknowledgement, not a new question), and keeping it in AgentCore keeps the harness logic-light.

### 3. Decoupling memory from normal agent construction

**Decision:** In `get_or_create_agent`, stop calling `get_memory_session_manager()` and pass `session_manager=None` to the `Agent` constructor. Slack metadata (speaker identity, channel ID) is injected into the prompt payload as conversational context, not as a memory selector.

**Why:** The spec requires that normal invocations have no automatic memory. The Strands `session_manager` parameter is the single coupling point — setting it to `None` cleanly satisfies this.

**Payload for normal invocation:**

```python
{
    "prompt": "<user message>",
    "image_b64": "<optional>",
    "speaker": {"user_id": "U123", "display_name": "alice", "real_name": "Alice Smith"},
    "channel_id": "C456",
    "thread_context": { ... }
}
```

The `X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Id` header is removed from `invoke_agentcore` for standard invocations. It is still sent for gate evaluations (since the gate agent has no memory coupling, this is harmless and maintains the existing session routing).

### 4. Memory tools via secondary agent invocation

**Decision:** Implement `channel_memory_store` and `channel_memory_recall` as `@tool`-decorated functions in `main.py`. Each constructs a short-lived secondary `Agent` with a memory session manager scoped to the channel namespace, sends a synthetic message, and returns the result.

**Why:** The spec requires memory operations go through a secondary agent. Constructing a Strands `Agent` with `session_manager` is the only way to interact with AgentCore Memory through the Strands framework — there is no standalone memory client.

**Memory namespace mapping:**

The channel ID becomes the `actor_id` for the memory session manager. This maps to the existing `agentcore.json` namespace templates:
- SEMANTIC: `/users/{channel_id}/facts` — stores channel-level facts
- SUMMARIZATION: `/summaries/{channel_id}/{session_id}` — stores conversation summaries per session within the channel

The `/users/` prefix in the template is a naming artifact of the existing config, not a semantic constraint. Using `channel_id` as `actor_id` effectively makes these channel-scoped namespaces.

**Memory session ID:** For memory tool invocations, the `session_id` is the same session ID derived from the Slack thread (`slack_{sha256(channel:thread_ts)}`). This preserves the summarization namespace's per-thread granularity within the channel scope.

**Store tool flow:**

```
channel_memory_store(content, channel_id, session_id)
  → build AgentCoreMemorySessionManager(actor_id=channel_id, session_id=session_id)
  → Agent(model=load_model(), session_manager=sm, tools=[], system_prompt=MEMORY_AGENT_PROMPT)
  → agent(f"Remember this for future conversations in this channel: {content}")
  → return confirmation
```

**Recall tool flow:**

```
channel_memory_recall(query, channel_id, session_id)
  → build AgentCoreMemorySessionManager(actor_id=channel_id, session_id=session_id)
  → Agent(model=load_model(), session_manager=sm, tools=[], system_prompt=MEMORY_AGENT_PROMPT)
  → agent(f"What do you know about: {query}")
  → return agent response (memory-hydrated context)
```

**Alternative considered:** Calling the AgentCore Memory API directly without a secondary agent. Rejected — the `AgentCoreMemorySessionManager` is tightly integrated with the Strands `Agent` lifecycle (it hooks into `before_model_invoke` and `after_model_invoke`). There is no public standalone memory read/write API exposed through the SDK.

### 5. Tool profile configuration

**Decision:** Define a `TOOL_PROFILES` dict in `main.py` that maps profile names to tool lists. Agent construction reads the appropriate profile.

```python
TOOL_PROFILES = {
    "primary": lambda: [get_return_policy, get_product_info, channel_memory_store, channel_memory_recall] + [c for c in mcp_clients if c],
    "memory": lambda: [],  # memory agent gets no tools — session_manager handles memory
}
```

Using callables (lambdas) so MCP clients and tools resolve at construction time, not import time.

**Why:** Satisfies the configuration-driven requirement without over-engineering. A dict is simple, explicit, and extensible. New profiles are one line.

**Alternative considered:** A formal registry/plugin system. Rejected — premature for two profiles. The dict can be promoted to a config file or registry later if the number of profiles grows.

### 6. Channel ID and session ID availability in tools

**Decision:** The `channel_id` and `session_id` are passed to the main agent as part of the prompt payload metadata. The memory tools access them through a module-level or closure-scoped reference set at agent construction time, rather than requiring the LLM to pass them as tool arguments.

**Why:** The LLM should not need to know or manage infrastructure identifiers. The channel and session are known at invocation time and are constant for the duration of the request. Making them implicit to the tool avoids hallucinated or incorrect IDs.

**Implementation:** `get_or_create_agent` stores `channel_id` and `session_id` in a request-scoped context (e.g., a module-level dict keyed by session_id, or closure variables captured when building the tool functions). The `@tool` functions read from this context.

### 7. System prompt rewrite

**Decision:** Replace the `SYSTEM_PROMPT` `<memory_architecture_and_rules>` block with guidance that:
- Explains memory is available through tools (`channel_memory_store`, `channel_memory_recall`)
- Instructs the agent to use thread context (supplied in payload) as the primary conversational state
- Directs the agent to recall memory only when prior channel knowledge would materially help
- Directs the agent to store memory only for durably useful information
- Treats memory results as supporting context, not authoritative

### 8. Silent ignore behavior

**Decision:** In `bot.py`, when the gate returns `IGNORE`: remove the `thinking_face` reaction and return. Do not add `+1` or any other reaction.

**Why:** Direct spec requirement. The current thumbs-up creates noise and signals the bot's presence on messages it chose not to answer.

### 9. Superseding memory entries on store

**Decision:** The `channel_memory_store` tool's system prompt instructs the secondary memory agent to frame updates as superseding records. When the main agent stores a resolution or change to a previously stored fact, the content sent to the memory agent includes explicit context from the original entry — topic, prior status, and the new status — so that the updated entry is semantically similar to the original and ranks at least as high on future recalls.

**Why:** AgentCore Memory's SEMANTIC strategy indexes facts by embedding similarity. If a resolution is stored as a standalone fact ("catalog issue is fixed"), it may score lower than the original report ("ongoing issue: stale product catalog") when a user asks "any product catalog issues?" because the original is a closer semantic match to the question. Including the original topic in the update ("RESOLVED: The stale product catalog issue originally reported as ongoing is now fixed") makes the embeddings overlap, giving the resolution a competitive relevance score.

**Implementation:** Two changes:
1. The `MEMORY_AGENT_PROMPT` instructs the memory agent to echo back the stored content in a form that references the topic being updated.
2. The main agent's `SYSTEM_PROMPT` guidance for `channel_memory_store` instructs it to include prior context when storing updates (e.g., "RESOLVED: [original issue description] is now [new status]").

**Alternative considered:** Deleting old entries before writing new ones. Rejected — the AgentCore Memory SDK does not expose a delete-by-content API through the Strands session manager, and even if it did, identifying which prior entry to delete requires a recall-then-delete sequence that adds latency and complexity.

### 10. Recall reconciliation via system prompt

**Decision:** Add guidance to the main agent's `SYSTEM_PROMPT` that when `channel_memory_recall` returns multiple entries about the same topic with contradictory statuses, the agent SHALL prefer the entry that reflects a resolution or more recent state. When only an older entry is returned with no contradiction, the agent should qualify it with temporal context rather than asserting it as definitively current.

**Why:** Even with superseding stores, there is no guarantee that the recall `top_k` window will always surface the newest entry first — embedding similarity and relevance scoring are not time-ordered. The agent needs explicit instruction to reconcile rather than naively surfacing the first match.

**Implementation:** Add a `<memory_reconciliation>` block to `SYSTEM_PROMPT` with rules:
- If multiple entries about the same topic are returned, prefer the one indicating resolution/completion/update.
- If a single entry is returned about an issue or status, qualify it (e.g., "as of the last update I have...") rather than stating it as absolute fact.
- Never assert that an issue is ongoing without checking whether a resolution exists.

### 11. Automatic memory pre-recall on first thread message

**Decision:** In the `invoke` entrypoint in `main.py`, when the session is new (session_id not in `_agents`), perform a channel memory recall before the main agent loop. This uses a dedicated pre-recall agent — a short-lived `Agent` with a memory session manager but no tools — whose system prompt instructs it to act as a relevance filter: given the user's message, return only channel history that would be useful to another agent answering that question, or return nothing.

**Why:** The first message in a thread has no prior conversational context. Without pre-recall, the agent starts cold and can only access channel memory if it explicitly calls `channel_memory_recall` — which it may not think to do. Automatic pre-recall ensures the agent benefits from stored channel knowledge on every new conversation without relying on the LLM's judgment to trigger the recall.

**Trigger:** New session detection via `session_id not in _agents`. This is checked before `get_or_create_agent` constructs the agent, so it fires exactly once per thread — the first time.

**Pre-recall agent construction:**

```python
PRE_RECALL_PROMPT = """You are a memory relevance filter. You will receive a user's message and 
have access to stored channel history via your memory context.

Your job is NOT to answer the user's question. Instead, determine what in the channel's stored 
history might be relevant to an agent who will answer this question.

If relevant history exists, return ONLY the relevant facts — concise, factual, no commentary.
If nothing in the channel history is relevant, return exactly: NO_RELEVANT_HISTORY"""
```

```
pre_recall(user_prompt, channel_id, session_id)
  → build AgentCoreMemorySessionManager(actor_id=channel_id, session_id=session_id)
  → Agent(model=load_model(), session_manager=sm, tools=[], system_prompt=PRE_RECALL_PROMPT)
  → agent(user_prompt)
  → if result != "NO_RELEVANT_HISTORY": prepend to main agent prompt
```

**Integration with main agent prompt:** The pre-recall result is injected as a `[Channel history]` block in the context prepended to the prompt, alongside speaker and thread context. The main agent sees it as background knowledge, not as a tool call result.

**Only on first message:** Subsequent messages in the same thread hit the cached agent in `_agents` and skip the pre-recall. The main agent can still call `channel_memory_recall` explicitly on any turn if it decides additional memory lookup would help.

**Failure handling:** If the pre-recall agent throws an exception or times out, log the error and proceed without injected memory. The pre-recall is a best-effort enrichment, not a gate.

**Alternative considered:** Running the pre-recall in the harness (bot.py) as a separate HTTP call. Rejected — this adds a second round-trip and moves memory infrastructure knowledge into the harness. Keeping it in `main.py` reuses the existing `_request_context`, `get_channel_memory_session_manager`, and `load_model()` path with zero new harness code.

**Alternative considered:** Always pre-recalling on every message, not just the first. Rejected — subsequent messages already have thread context and the cached agent's conversation history. Pre-recalling on every message adds latency without proportional benefit, and the agent can still recall explicitly when needed.

## Risks / Trade-offs

**[Increased Slack API calls] → Mitigation: caching and limiting**
Every ambient message now triggers `users.info` + `conversations.replies`. For high-traffic channels this adds latency. Mitigation: cache `users.info` results in-memory (user profiles change rarely), and cap `conversations.replies` with `limit=10`. The thread lock (`thread_locks[thread_ts]`) already serializes per-thread, so Slack rate limits are unlikely to be hit per-thread.

**[Secondary agent latency for memory tools] → Mitigation: acceptable for deliberate operations**
Memory store/recall each instantiate a Strands Agent, which involves a model call through AgentCore Runtime. This adds ~1-3s per memory operation. Since memory is deliberate (not automatic), this latency only occurs when the agent decides memory is valuable — it is not on the critical path of every message.

**[Channel memory namespace reuses `/users/` template prefix] → Mitigation: semantic, not structural**
The existing `agentcore.json` namespace template is `/users/{actorId}/facts`. Passing `channel_id` as `actorId` produces paths like `/users/C123ABC/facts`. The `/users/` prefix is misleading but functionally correct — AgentCore Memory treats it as an opaque namespace path. Changing the template would require an `agentcore.json` update and redeployment, which is a non-goal of this change. Document the naming discrepancy in code comments.

**[Agent cache invalidation with new tool closures] → Mitigation: re-examine cache keying**
The current `_agents` dict caches agents by `session_id`. If memory tools capture `channel_id` via closure at construction time, the cached agent is correct as long as `channel_id` doesn't change for a given session (it won't — a session is tied to a Slack thread, which is in one channel). No change to cache keying needed.

**[Gate evaluator sees more context → higher token usage] → Mitigation: bounded context**
Thread context is capped at 10 recent messages. The gate evaluator is a lightweight classifier — the additional context improves accuracy and is worth the marginal token cost.

**[Pre-recall adds latency on first message] → Mitigation: first-message-only, best-effort**
The pre-recall agent adds ~1-3s to the first message in every new thread. This is acceptable because: (1) only the first message pays this cost — subsequent messages skip it; (2) the first message already has the highest perceived latency (no cached agent, no thread context); (3) the pre-recall is best-effort — failure doesn't block the response.
