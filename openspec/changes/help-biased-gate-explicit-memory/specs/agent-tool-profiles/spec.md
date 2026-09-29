# Spec Delta

## Purpose

Provides independently configurable tool sets for the primary conversational agent and nested specialized agents, preventing recursive tool loops and supporting future agent specialization.

## ADDED Requirements

### Requirement: Primary agent receives full toolset

The primary conversational agent SHALL be constructed with the complete application toolset: domain-specific tools (product info, return policy), MCP clients (web search, gateway), and memory tools (channel store, channel recall).

#### Scenario: Primary agent tool inventory

- **WHEN** the primary agent is constructed for a standard invocation
- **THEN** its tool list includes domain tools, available MCP clients, and channel memory tools

### Requirement: Nested memory agent receives restricted toolset

A nested agent invoked by a memory tool SHALL be constructed with only the capabilities necessary for the memory operation. It SHALL NOT receive domain tools, MCP clients, or memory tools.

#### Scenario: Memory agent tool inventory

- **WHEN** a memory tool constructs a secondary agent for a memory operation
- **THEN** that agent has no tools beyond what the memory session manager provides — specifically, no `channel_memory_store`, no `channel_memory_recall`, no domain tools, and no MCP clients

### Requirement: No recursive memory tool access

A nested memory agent SHALL NOT have access to tools that would invoke another memory agent. This prevents unbounded agent-to-agent recursion.

#### Scenario: Memory agent cannot call memory tools

- **WHEN** a nested memory agent is constructed
- **THEN** its tool configuration does not include any tool that would construct another nested agent

### Requirement: Configuration-driven tool exposure

Tool sets for each agent profile SHALL be defined through configuration rather than hard-coded lists, so that future specialized agent profiles can be added without restructuring agent construction logic.

#### Scenario: Adding a new agent profile

- **WHEN** a new specialized agent role is needed (e.g., a summarization agent)
- **THEN** its tool set can be defined by configuring a new profile without modifying the tool-assignment logic for existing profiles

#### Scenario: Primary agent profile change

- **WHEN** a new tool is added to the application
- **THEN** updating the primary agent's tool profile configuration is sufficient to make it available to the primary agent without modifying nested agent profiles
