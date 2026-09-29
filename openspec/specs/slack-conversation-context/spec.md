# Spec: slack-conversation-context

## Purpose

Provides speaker identity, thread participation signals, and recent thread messages so the Slack harness can make informed ambient-routing decisions and supply conversational context to the AgentCore agent.

## Requirements

### Requirement: Speaker identity resolution

The harness SHALL resolve the Slack user ID of each incoming message into a speaker identity containing the user's display name and real name, using the Slack `users.info` API.

#### Scenario: Known Slack user sends a message

- **WHEN** a message arrives from a Slack user with ID `U12345`
- **THEN** the harness resolves the user's display name and real name from Slack before routing or invoking the agent

#### Scenario: User lookup fails

- **WHEN** the Slack `users.info` call fails or times out
- **THEN** the harness proceeds with the raw Slack user ID as the speaker identity and does not block message processing

### Requirement: Thread participation inspection

The harness SHALL inspect the current thread using the Slack `conversations.replies` API to determine: the list of participants (human and bot), whether the bot has already posted a reply in the thread, and whether the message sender is the only human participant so far.

#### Scenario: Bot has previously replied in the thread

- **WHEN** a message arrives in a thread where the bot has at least one prior reply
- **THEN** the thread context includes a `bot_has_participated: true` signal

#### Scenario: Multiple human participants in thread

- **WHEN** a message arrives in a thread where two or more distinct human users have posted
- **THEN** the thread context includes `sender_is_sole_human: false`

#### Scenario: New top-level message (no thread yet)

- **WHEN** a message arrives that is not a reply to an existing thread
- **THEN** the thread context indicates zero prior replies, `bot_has_participated: false`, and `sender_is_sole_human: true`

### Requirement: Recent thread context collection

The harness SHALL collect recent messages from the current thread and make them available as conversational context for both the gate evaluator and the main agent invocation.

#### Scenario: Thread with prior messages

- **WHEN** a message arrives in a thread that already has previous messages
- **THEN** the harness includes the most recent thread messages (up to a configurable limit) in the payload sent to AgentCore

#### Scenario: Thread context included in gate evaluation

- **WHEN** the harness evaluates an untagged ambient message through the gate
- **THEN** the gate evaluation payload includes the thread context, participant list, and bot-participation flag alongside the message text

### Requirement: Help-biased ambient gate routing

The ambient gate SHALL favor responding over ignoring. Untagged messages SHALL be answered when they plausibly request help, ask a question, or continue an active interaction with the bot. `IGNORE` SHALL be reserved for messages that are clearly human-to-human chatter, social acknowledgements, or conversation unrelated to the bot's domain.

#### Scenario: Ambiguous message in thread where bot has participated

- **WHEN** an untagged message arrives in a thread where the bot has previously replied, and the message is ambiguous (could be directed at the bot or at another human)
- **THEN** the gate classifies the message as `RESPOND`

#### Scenario: Clear human-to-human chatter

- **WHEN** an untagged message is clearly social chatter between humans (e.g., "hey are you coming to lunch?") in a thread where the bot has not participated
- **THEN** the gate classifies the message as `IGNORE`

#### Scenario: Question that could be a support request

- **WHEN** an untagged message asks a question that could plausibly be a customer support request (e.g., "does anyone know the return window for headphones?")
- **THEN** the gate classifies the message as `RESPOND`

### Requirement: Silent ignore behavior

When the ambient gate classifies a message as `IGNORE`, the harness SHALL take no visible action — no reply and no emoji reaction. The previous behavior of adding a thumbs-up reaction on ignore SHALL be removed.

#### Scenario: Gate returns IGNORE

- **WHEN** the gate evaluates a message and returns `IGNORE`
- **THEN** the harness removes any in-progress reaction (e.g., thinking-face) and adds no new reaction or message

### Requirement: Conversational context in agent invocation

The main agent invocation payload SHALL include the resolved speaker identity and recent thread messages so the agent can understand the ongoing conversation without depending on AgentCore Memory for thread continuity.

#### Scenario: Standard invocation with thread context

- **WHEN** the harness invokes the main agent for a message in a thread with prior messages
- **THEN** the payload includes the speaker's display name, recent thread messages, and thread participation metadata alongside the user's prompt
