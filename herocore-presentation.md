# Herocore

### A Slack-to-AgentCore Bridge Framework with Bidirectional Tool Proxying

---

## The Problem

You have an AI agent running in Amazon Bedrock AgentCore.
You have users in Slack.
You have tools and services scattered across environments that AgentCore may or may not be able to reach.

How do you connect all three — without duct-taping together an LLM-powered thread scraper, a Redis state store, and a prayer?

---

## The Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Docker Compose (your environment)                                  │
│                                                                     │
│  ┌──────────────┐   ┌──────────────────┐   ┌────────────────────┐  │
│  │  mcp-github   │   │  mcp-filesystem   │   │  (your MCP tool)   │  │
│  │  :8082/mcp    │   │  :8000/mcp        │   │  :XXXX/mcp         │  │
│  └──────┬───────┘   └──────┬───────────┘   └────────┬───────────┘  │
│         │                  │                         │              │
│         └──────────┬───────┴─────────────────────────┘              │
│                    │  JSON-RPC / HTTP                                │
│              ┌─────┴──────────────────────────────────────┐         │
│              │           HARNESS (bot.py)                  │         │
│              │                                             │         │
│              │  • Slack Socket Mode (WebSocket)            │         │
│              │  • Gate routing & emoji feedback            │         │
│              │  • Throttling, banning, thread locking      │         │
│              │  • MCP tool executor (local tools)          │         │
│              │  • WebSocket bridge to AgentCore            │         │
│              └─────────────┬───────────────────────────────┘         │
│                            │  WebSocket (wss://)                    │
└────────────────────────────┼────────────────────────────────────────┘
                             │  OAuth M2M (client_credentials)
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Amazon Bedrock AgentCore                                           │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  CustomerSupport Runtime (CUSTOM_JWT auth)                   │   │
│  │                                                               │   │
│  │  agent__ tools (cloud-side):                                  │   │
│  │    • filesystem (sandboxed read-only)                         │   │
│  │    • http_get / http_head                                     │   │
│  │    • channel_memory_store / channel_memory_recall             │   │
│  │                                                               │   │
│  │  client__ tools (proxied from harness via WebSocket):         │   │
│  │    • client__read_file, client__list_directory, ...           │   │
│  │    • client__get_file_contents, client__create_pull_request   │   │
│  │                                                               │   │
│  │  Sub-agents (sessionless):                                    │   │
│  │    • Gate evaluator (RESPOND / IGNORE / INAPPROPRIATE)        │   │
│  │    • Memory recall / store                                    │   │
│  │    • Web content extraction                                   │   │
│  │    • Pre-recall relevance check                               │   │
│  └──────────┬──────────────────────────────────────┬─────────────┘   │
│             │                                      │                │
│    ┌────────┴────────┐                  ┌──────────┴──────────┐    │
│    │  AgentCore       │                  │  AgentCore Gateway   │    │
│    │  Memory          │                  │  (WarrantyCheck      │    │
│    │  (SharedMemory)  │                  │   Lambda target)     │    │
│    └─────────────────┘                  └─────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Part 1: The Harness

### What the harness does

The harness is the layer between Slack and AgentCore. It is **not** a thin proxy — it is an opinionated runtime that handles everything the cloud agent shouldn't have to think about.

---

### 1A: Slack Bridge (Socket Mode)

The harness connects to Slack via **Socket Mode** — a WebSocket-based protocol that requires no public endpoint. No ingress rules, no load balancer, no webhook URL rotation.

```python
# bot.py — startup
app = App(token=SLACK_BOT_TOKEN)
handler = SocketModeHandler(app, SLACK_APP_TOKEN)
handler.start()
```

Every Slack message event flows through the harness. The harness:

- Identifies if the bot was `@`-mentioned or if it's an ambient channel message
- Downloads and base64-encodes image attachments for multimodal payloads
- Fetches recent thread history to build conversational context
- Maps Slack threads to deterministic session IDs: `slack_{sha256(channel:thread_ts)}`

---

### 1B: MCP Tool Proxy

The harness runs **local MCP tool servers** (via Docker Compose sidecars) and proxies them to AgentCore over a **bidirectional WebSocket**.

```
Agent thinks: "I need to read a file on the client side"
    ↓
AgentCore sends:  {"type": "tool_call", "name": "client__read_file", ...}
    ↓  (WebSocket)
Harness receives, executes locally via JSON-RPC to mcp-filesystem
    ↓
Harness sends:    {"type": "tool_result", "content": "...file contents..."}
    ↓  (WebSocket)
Agent continues with the result
```

Tool definitions come from `mcp_tools.json` — a declarative config that maps tool names to MCP server endpoints, transport types, and auth:

```json
{
  "servers": [
    {
      "name": "github",
      "transport": "http",
      "url": "http://mcp-github:8082/mcp",
      "auth": { "type": "bearer", "token": "${GITHUB_TOKEN}" },
      "tools": [
        { "name": "create_pull_request", "description": "...", "input_schema": {...} }
      ]
    }
  ]
}
```

Auth tokens support `${ENV_VAR}` references — resolved at runtime from the container's environment. Supported auth types: `bearer`, `header`, `basic`.

---

### 1C: Harness Behaviors

The harness owns the UX layer — the agent never has to deal with rate limits, abuse, or visual feedback.

**Gate routing** — Two-phase message processing:
1. Ambient messages (not `@`-mentioned) go through a **gate evaluation** — a sessionless sub-agent call that classifies as `RESPOND`, `IGNORE`, or `INAPPROPRIATE`
2. Direct mentions skip the gate entirely

**Emoji feedback** — Visual status via Slack reactions:
| State | Emoji |
|-------|-------|
| Evaluating gate | `thinking_face` |
| Generating response | `hourglass` |
| Complete | `bulb` |
| Ignoring (ambient) | `thumbsup` |
| Inappropriate | `point_up` |
| Banned user | `see_no_evil` `hear_no_evil` `speak_no_evil` |

**Throttling** — Per-user rate limiting (configurable window, default 5s). Rapid-fire messages silently dropped.

**Banning** — Strike-based system. Inappropriate messages accumulate strikes; exceeding the threshold triggers a temporary ban. All configurable:
- `MAX_WARNINGS` (default 3)
- `WARNING_WINDOW_MIN` (default 10 min)
- `BAN_DURATION_MIN` (default 60 min)

**Thread locking** — One message processed at a time per thread (via `threading.Lock`), preventing race conditions.

**Thread silencing** — Users can say `@bot silence` to permanently mute the bot in a thread.

---

## Part 2: The AgentCore Layer

### Exposing tools to the agent

The agent sees a unified tool surface — both **cloud-side** tools running inside AgentCore and **client-side** tools proxied from the harness. Namespacing prevents collisions:

| Prefix | Source | Examples |
|--------|--------|----------|
| `agent__` | Cloud-side (AgentCore) | `agent__read_file`, `agent__http_get`, `agent__channel_memory_store` |
| `client__` | Harness-side (proxied) | `client__read_file`, `client__create_pull_request`, `client__search_code` |

The agent can read files in its own cloud sandbox (`agent__read_file`) **and** read files in the harness environment (`client__read_file`). Same tool concept, different execution context.

---

### Thread state without LLMs and Redis

Traditional Slack bots reconstruct conversation state by either:
- Scraping the Slack API for thread history on every request
- Storing thread state in Redis, using an LLM to summarize and compress
- Both, expensively

Herocore does neither. Instead, it uses **two headers** to let AgentCore manage state natively:

**Session ID** — `slack_{sha256(channel:thread_ts)}`
- One AgentCore session per Slack thread
- The agent instance is cached in memory, keyed by session ID
- Conversational continuity comes from the Slack thread context injected into each request

**User ID** — passed via `x-amzn-bedrock-agentcore-runtime-custom-user-id`
- Maps to the channel ID (channel-scoped, not user-scoped)
- Controls memory namespace: `/users/{channel_id}/facts` and `/summaries/{channel_id}/{session_id}`

The Slack thread **is** the conversation state. The harness fetches the last N messages (configurable via `THREAD_CONTEXT_LIMIT`) and injects them as context. No separate state store needed.

---

### Sub-agent flows

The AgentCore layer spawns **sessionless sub-agents** for focused tasks that shouldn't pollute the main agent's context or tool access:

**Gate evaluator** — Classifies ambient messages with no tools, no session. Pure classification.

**Memory agents** — `channel_memory_store` and `channel_memory_recall` use dedicated agents with empty tool profiles (preventing recursion) but with a `session_manager` wired to AgentCore Memory.

**Pre-recall agent** — On the first message in a new session, checks if stored channel memory is relevant to the incoming prompt. Returns structured facts or `NO_RELEVANT_HISTORY`. This avoids polluting every conversation with irrelevant historical context.

**Web extraction agent** — When `http_get` fetches a page, a nested agent distills the raw HTML into content relevant to the current conversation thread.

All of these run in a **sessionless context** — they execute, return a result, and are discarded. No cached state, no tool access beyond what they specifically need.

---

## Part 3: My Implementation

### Orchestrating with Docker Compose

As a user of the framework, I orchestrated three containers:

```yaml
services:
  harness:
    build: .
    env_file: .env
    volumes:
      - ./mcp_tools.json:/app/mcp_tools.json:ro

  mcp-filesystem:
    build:
      dockerfile: Dockerfile.mcp-filesystem
    volumes:
      - .:/workspace:ro      # The agent can see its own codebase

  mcp-github:
    image: ghcr.io/github/github-mcp-server
    command: ["http", "--port", "8082"]
    environment:
      GITHUB_PERSONAL_ACCESS_TOKEN: ${GITHUB_TOKEN}
```

The filesystem MCP server mounts the project root as `/workspace` (read-only). The GitHub MCP server connects to GitHub with a PAT. Both expose Streamable HTTP endpoints that the harness proxies to AgentCore.

### The result: an agent that can modify itself

With this setup, the agent can:

1. **Read its own source code** via `client__read_file` and `client__list_directory`
2. **Search across the codebase** via `client__search_files` and `client__search_code`
3. **Create branches** via `client__create_branch`
4. **Commit changes** via `client__create_or_update_file`
5. **Open pull requests** via `client__create_pull_request`

A user in Slack can say *"the gate evaluation prompt is too aggressive, can you make it more lenient?"* — and the agent will explore the code, propose a change, create a branch, commit it, and open a PR. All from a Slack message.

---

## Part 4: The Vision — A Configurable Bridge Image

### The problem Herocore solves

It is unclear whether every AgentCore instance will be able to reach into the right environments — your VPC, your internal APIs, your on-prem services. Network topology, security boundaries, and GDP compliance vary wildly across deployments.

But **you know where your harness is**. You know what it can access. And it can connect to AgentCore with OAuth (M2M client credentials).

### The idea

Ship a **single container image** that is configured entirely via environment variables and secrets. No config files mounted into the image. No custom code required.

```
┌──────────────────────────────────────────────────────┐
│  Herocore Container                                   │
│                                                       │
│  Configured via env vars:                             │
│    SLACK_APP_TOKEN, SLACK_BOT_TOKEN                   │
│    COGNITO_CLIENT_ID, COGNITO_CLIENT_SECRET           │
│    COGNITO_DOMAIN, AGENT_URL                          │
│    USE_WEBSOCKET=true                                 │
│    GITHUB_TOKEN, THROTTLE_WINDOW_SEC, ...             │
│                                                       │
│  Configured via mcp_tools.json (baked or mounted):    │
│    Server endpoints, tool schemas, auth configs       │
│    Auth tokens reference ${ENV_VARS}                  │
│                                                       │
│  What it does:                                        │
│    1. Bridges Slack ↔ AgentCore (WebSocket)           │
│    2. Proxies harness-side MCP tools to the agent     │
│    3. Handles gate, throttle, ban, emoji, threads     │
└──────────────────────────────────────────────────────┘
```

### Why proxy instead of direct access?

AgentCore runs in AWS. Your tools might run anywhere — a corporate network, a different cloud, behind a VPN. Rather than punching holes in network boundaries or running everything in the same VPC, the harness acts as a **bridge**:

- The harness **sits where the tools are** (or where it has network access to them)
- The harness **authenticates to AgentCore** via Cognito M2M OAuth
- The harness **exposes its local access** to the agent via the WebSocket proxy protocol

The agent doesn't need to know where the tools physically run. It calls `client__read_file` and gets a result. The harness handles the routing.

### Deployer responsibility: auth injection

Deployers of Herocore instances are responsible for injecting the right authentication into the headers. The `mcp_tools.json` format supports this natively:

```json
{
  "auth": {
    "type": "bearer",
    "token": "${MY_SERVICE_TOKEN}"
  }
}
```

or

```json
{
  "auth": {
    "type": "header",
    "name": "X-API-Key",
    "value": "${MY_API_KEY}"
  }
}
```

The deployer sets the environment variable. The harness resolves it at runtime. The agent never sees the raw credential — it just gets tool results.

This means a team can deploy a Herocore instance, point it at their MCP-compatible tools, inject their auth tokens as environment variables, and have a fully functional Slack-to-AgentCore bridge with client-side tool proxying — without writing a single line of code.

---

## Demo

### What we'll see

1. **A Slack message triggers the gate** — watch the emoji reactions cycle through `thinking_face` → `hourglass` → `bulb`
2. **The agent uses cloud-side tools** — reading files in AgentCore's sandbox, making HTTP requests
3. **The agent uses client-side tools** — reading the codebase from the harness environment, searching GitHub
4. **Channel memory persists across threads** — the agent recalls context from earlier conversations in the same channel
5. **The agent opens a PR** — from a Slack conversation, the agent creates a branch, commits a change, and opens a pull request

### Key things to notice

- **No Redis, no thread state store** — the Slack thread is the state; session headers handle the rest
- **No LLM summarization of history** — thread context is injected directly from Slack
- **Tools from two environments** — the agent seamlessly uses both `agent__` and `client__` tools without knowing the difference
- **All configuration is env vars** — swap out the MCP tools, change the auth, point at a different AgentCore — no code changes

---

## Summary

| Layer | Responsibility |
|-------|---------------|
| **Harness** | Slack bridge, MCP tool proxy, gate routing, emoji UX, throttling, banning, thread management |
| **AgentCore** | Agent runtime, cloud-side tools, memory, sub-agent flows, tool namespace unification |
| **herocore-bridge** | Shared library — WebSocket protocol, tool execution, auth resolution, config parsing |
| **mcp_tools.json** | Declarative tool configuration — endpoints, schemas, auth (all env-var resolvable) |
| **Docker Compose** | Orchestration of harness + MCP tool sidecars |

The core insight: **put the bridge where the access is, and proxy it to where the intelligence is**.
