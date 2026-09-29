# Spec Delta

## Purpose

Provides deliberate channel-scoped memory read/write operations as main-agent tools, replacing automatic memory coupling so persistent memory is only accessed when the agent explicitly chooses to use it.

## ADDED Requirements

### Requirement: No automatic memory on normal invocations

Normal conversational invocations SHALL NOT automatically attach an AgentCore Memory session manager. The agent constructed for standard message handling SHALL have no implicit memory identity derived from Slack channel ID, user ID, or any combination thereof.

#### Scenario: Standard message invocation

- **WHEN** the harness invokes the main agent for a normal user message
- **THEN** the agent is constructed without a memory session manager and no persistent memory is automatically read or written

#### Scenario: Slack metadata does not select memory namespace

- **WHEN** the harness passes Slack user identity and channel information as conversational metadata
- **THEN** that metadata is available to the agent as context about who is speaking but does not implicitly select or activate a memory namespace

### Requirement: Channel memory store tool

The main agent SHALL have access to a `channel_memory_store` tool that writes durable information scoped to the current Slack channel. The agent invokes this tool only when it determines that information is likely to remain useful beyond the active thread.

#### Scenario: Agent stores channel knowledge

- **WHEN** the agent invokes `channel_memory_store` with content and the current channel identifier
- **THEN** the information is persisted in a channel-scoped memory namespace and is retrievable in future conversations in that channel

#### Scenario: Agent does not store routine conversation

- **WHEN** a routine support interaction completes with no information the agent judges as durably useful
- **THEN** the agent does not invoke `channel_memory_store`

### Requirement: Channel memory recall tool

The main agent SHALL have access to a `channel_memory_recall` tool that queries previously stored channel memory. The agent invokes this tool when prior channel knowledge would materially improve its response.

#### Scenario: Agent recalls relevant channel history

- **WHEN** the agent invokes `channel_memory_recall` with a query and the current channel identifier
- **THEN** the tool returns matching stored information from that channel's memory namespace

#### Scenario: No relevant memory found

- **WHEN** the agent invokes `channel_memory_recall` and no relevant information exists in the channel's memory
- **THEN** the tool returns an empty or no-results response and the agent continues without memory context

### Requirement: Memory implemented via secondary agent invocation

Memory tools SHALL execute their operations through a secondary AgentCore agent invocation — a specialized memory agent constructed with a memory-scoped session manager — rather than giving the primary conversational agent direct memory session access.

#### Scenario: Memory store operation

- **WHEN** the main agent invokes `channel_memory_store`
- **THEN** the tool internally constructs a secondary agent with a memory session manager scoped to the channel namespace and sends the content as a synthetic message to persist it

#### Scenario: Memory recall operation

- **WHEN** the main agent invokes `channel_memory_recall`
- **THEN** the tool internally constructs a secondary agent with a memory session manager scoped to the channel namespace, sends the query, and returns the retrieved context to the main agent

### Requirement: Automatic memory pre-recall on first thread message

On the first message of a new session (no cached agent for this session ID), the AgentCore entrypoint SHALL automatically perform a channel memory recall before the main agent processes the user's prompt. The recall uses a dedicated pre-recall agent whose purpose is to determine what previously stored channel knowledge, if any, is relevant to the incoming message. The pre-recall agent SHALL NOT attempt to answer the user's question — it SHALL only return relevant historical context or nothing.

#### Scenario: First message with relevant channel history

- **WHEN** a user sends the first message in a new thread and channel memory contains information relevant to that message
- **THEN** the pre-recall agent returns the relevant context, which is prepended to the main agent's prompt as background knowledge

#### Scenario: First message with no relevant channel history

- **WHEN** a user sends the first message in a new thread and channel memory contains nothing relevant
- **THEN** the pre-recall agent returns nothing and the main agent receives no injected memory context

#### Scenario: Subsequent messages in same thread

- **WHEN** a user sends a follow-up message in an existing thread (agent already cached for this session)
- **THEN** no automatic pre-recall occurs — the main agent may still call `channel_memory_recall` explicitly if it chooses

#### Scenario: Pre-recall failure

- **WHEN** the pre-recall agent invocation fails (timeout, error, memory service unavailable)
- **THEN** the main agent proceeds without injected memory context and the failure is not surfaced to the user

### Requirement: Memory updates supersede prior entries

When the agent stores information that updates, resolves, or contradicts a previously stored fact, the stored content SHALL include enough context from the original entry that a future semantic recall for the original topic returns the updated entry at equal or higher relevance. The agent SHALL frame updates as superseding records rather than standalone new facts.

#### Scenario: Issue reported then resolved

- **WHEN** the agent previously stored "ongoing issue: stale product catalog" and later stores a resolution
- **THEN** the resolution entry includes the original topic context (e.g., "RESOLVED: The stale product catalog issue is now fixed") so that a future recall for "product catalog issue" returns the resolution

#### Scenario: Preference changed

- **WHEN** the agent previously stored a team preference and the user updates it
- **THEN** the new entry references the prior preference and states the replacement (e.g., "UPDATED: Team return policy preference changed from store credit to full refund")

### Requirement: Recall reconciliation of contradictory entries

When a memory recall returns multiple entries about the same topic that contradict each other, the agent SHALL prefer the entry that reflects the most recent state and SHALL NOT present outdated information as current without noting the contradiction.

#### Scenario: Recall returns both an issue report and its resolution

- **WHEN** `channel_memory_recall` returns entries indicating both an open issue and a resolved status for the same topic
- **THEN** the agent treats the resolution as authoritative and does not present the issue as still open

#### Scenario: Recall returns only the older entry

- **WHEN** `channel_memory_recall` returns only an older entry about a topic that may have been updated in a different thread
- **THEN** the agent presents the recalled information but qualifies it with the date or context it was stored, rather than asserting it as definitely current

### Requirement: Memory is optional enrichment

The main agent's system prompt SHALL instruct it to treat persistent memory as optional supporting context. Memory recall results SHALL not be assumed authoritative or complete. The agent SHALL not depend on memory for understanding the active Slack thread — that context comes from the thread messages supplied in the invocation payload.

#### Scenario: Memory recall returns stale information

- **WHEN** the agent recalls channel memory that conflicts with information visible in the current thread
- **THEN** the agent treats the current thread as authoritative and does not surface the stale memory to the user

#### Scenario: Memory service unavailable

- **WHEN** a memory tool invocation fails (secondary agent construction or invocation error)
- **THEN** the main agent continues the conversation without memory context and does not surface the failure to the user as a blocking error
