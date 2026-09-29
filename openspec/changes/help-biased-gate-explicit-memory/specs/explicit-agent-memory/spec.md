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

### Requirement: Memory is optional enrichment

The main agent's system prompt SHALL instruct it to treat persistent memory as optional supporting context. Memory recall results SHALL not be assumed authoritative or complete. The agent SHALL not depend on memory for understanding the active Slack thread — that context comes from the thread messages supplied in the invocation payload.

#### Scenario: Memory recall returns stale information

- **WHEN** the agent recalls channel memory that conflicts with information visible in the current thread
- **THEN** the agent treats the current thread as authoritative and does not surface the stale memory to the user

#### Scenario: Memory service unavailable

- **WHEN** a memory tool invocation fails (secondary agent construction or invocation error)
- **THEN** the main agent continues the conversation without memory context and does not surface the failure to the user as a blocking error
