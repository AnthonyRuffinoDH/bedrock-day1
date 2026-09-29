# Proposal

## Why

The Slack harness and AgentCore integration make two behaviors too implicit, causing the bot to under-respond and over-remember.

1. **Conservative ambient gate**: The gate evaluator in `main.py:143-163` classifies untagged messages with a bias toward `IGNORE`, and `bot.py:220-224` reacts with a thumbs-up for every ignored message — announcing the decision not to help. Ambiguous messages that a support bot should answer get dropped silently (or with a misleading reaction).

2. **Automatic memory coupling**: `memory/session.py` wires AgentCore Memory into every agent invocation via `session_manager`. The Slack channel ID (`bot.py:82`) becomes the memory actor identity, meaning every conversation automatically reads from and writes to persistent memory namespaces (`/users/{channel_id}/facts`, `/summaries/{channel_id}/{session_id}`). There is no way for the agent to choose whether memory is relevant — it is always injected.

These defaults need to invert: the bot should err toward helping, and memory should be deliberate.

## What Changes

* **Help-biased ambient routing**

  * Untagged messages SHALL be answered when they plausibly request help or continue an active bot interaction in the thread.
  * `IGNORE` SHALL be reserved for messages that are clearly human-to-human chatter, acknowledgements, or unrelated conversation.
  * Ambiguous cases SHALL favor responding.
  * The gate evaluation payload (`bot.py:211`) SHALL include Slack thread and participant context so the classifier can make an informed decision.

* **Slack conversation context enrichment**

  * `bot.py` SHALL resolve the current Slack user's ID and display name via Slack APIs.
  * `bot.py` SHALL inspect the thread to determine participants, whether the bot has already replied, and whether the sender is the only human participant so far.
  * Recent thread messages SHALL be available to both the gate evaluator and the main agent invocation, making Slack the authoritative source for active-thread conversational context.

* **Remove automatic memory identity from normal invocations**

  * **BREAKING**: `bot.py:82` SHALL stop sending the channel ID as `X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Id` for standard invocations.
  * **BREAKING**: `main.py:94` SHALL stop constructing an `AgentCoreMemorySessionManager` in `get_or_create_agent`. The agent returned for normal conversation SHALL have `session_manager=None`.
  * Slack user information SHALL still be passed as conversational metadata (so the agent knows who is speaking), but it SHALL NOT implicitly select a memory namespace.

* **Stop automatic memory hydration**

  * Persistent memory SHALL only be read or written when the main agent explicitly invokes a memory tool.
  * The `SYSTEM_PROMPT` memory architecture rules in `main.py:17-28` SHALL be replaced with guidance that treats memory as an optional, deliberate capability.

* **Introduce explicit channel-scoped memory tools**

  * A `channel_memory_store` tool: stores durable information scoped to the Slack channel, intended to be useful to future conversations in that channel.
  * A `channel_memory_recall` tool: queries previously stored channel memory when prior knowledge may help answer the current request.
  * Memory operations SHALL be implemented through a secondary AgentCore invocation — a specialized memory agent with its own restricted tool profile — rather than giving the main conversational agent direct memory session access.

* **Agent tool profiles**

  * The primary conversational agent SHALL receive the full toolset including memory tools, MCP clients, and domain tools.
  * The nested memory agent SHALL receive only the tools necessary for memory operations (no recursive memory-tool access, no customer support tools, no MCP clients).
  * Tool exposure SHALL be configuration-driven to support future specialized agent profiles.

* **Remove thumbs-up reaction on ignore**

  * When the ambient gate chooses `IGNORE`, the bot SHALL remain silent — no reaction, no message (`bot.py:222-223` removed).

## Capabilities

### New Capabilities

- `slack-conversation-context`: Provides speaker identity, thread participation history, recent thread messages, and signals used by the ambient gate to determine whether an untagged Slack message likely expects assistance. Enriches both gate evaluation and main agent invocation payloads.

- `explicit-agent-memory`: Provides deliberate channel-scoped memory read/write operations exposed as main-agent tools. Memory is implemented through a secondary AgentCore agent invocation with restricted tool access, decoupled from normal conversational execution.

- `agent-tool-profiles`: Provides independently configurable tool sets for the primary conversational agent and nested/specialized agent invocations, with recursion prevention ensuring memory agents cannot invoke memory tools on themselves.

### Modified Capabilities

*(No existing specs to modify — this is the first OpenSpec change in the project.)*

## Impact

* **Slack harness (`bot.py`)**
  * New Slack API calls: `users.info` for speaker identity, `conversations.replies` for thread context.
  * Gate evaluation payload grows to include thread context, participant list, and bot-participation flag.
  * Normal invocation payload grows to include speaker metadata and recent thread messages.
  * `invoke_agentcore` stops sending `X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Id` for standard invocations.
  * Thumbs-up reaction on `IGNORE` is removed; thinking-face is simply removed silently.

* **AgentCore entrypoint (`main.py`)**
  * `get_or_create_agent` stops constructing memory session managers — `session_manager=None`.
  * `SYSTEM_PROMPT` rewritten: removes automatic memory architecture rules, adds guidance for deliberate memory tool usage.
  * New memory tool functions (`channel_memory_store`, `channel_memory_recall`) added to primary agent's tool list.
  * Memory tools internally construct a secondary Agent with restricted tools and a memory-scoped session manager.
  * Gate evaluator prompt updated to accept and reason over thread context signals.

* **Memory integration (`memory/session.py`)**
  * `get_memory_session_manager` is no longer called from `get_or_create_agent`.
  * It is repurposed (or a new factory is created) for use by the nested memory agent, scoped to channel-only namespaces.
  * Memory namespace changes from `/users/{channel_id}/facts` to a channel-keyed namespace (e.g., `/channels/{channel_id}/facts`).

* **Infrastructure (`agentcore.json`)**
  * No changes expected — memory and runtime resources already exist. The shift is in how the application uses them, not in what is provisioned.
