# CustomerSupport Agent — Project Guide

## What This Is

A customer support agent deployed on Amazon Bedrock AgentCore, fronted by a Slack bot.
Workshop-originated project being extended into a real integration.

## Architecture

```
Slack (Socket Mode)
  └─ bot.py (harness)
       ├─ Gate evaluation:  POST /invocations { action: "evaluate_gate", prompt: ... }
       ├─ Normal invoke:    POST /invocations { prompt: ..., image_b64?: ... }
       └─ Auth: Cognito M2M OAuth (client_credentials grant)

AgentCore Runtime (CUSTOM_JWT auth)
  └─ app/CustomerSupport/main.py (entrypoint)
       ├─ Per-session agent cache (_agents dict, keyed by session_id)
       ├─ Tools: get_return_policy, get_product_info, MCP clients (Exa, Gateway)
       ├─ Memory: AgentCoreMemorySessionManager (auto-coupled via session_manager)
       └─ Model: loaded via model/load.py (Bedrock)

AgentCore Gateway (my-gateway, NONE auth)
  └─ WarrantyCheck Lambda target

AgentCore Memory (SharedMemory)
  └─ Strategies: SEMANTIC (/users/{actorId}/facts), SUMMARIZATION (/summaries/{actorId}/{sessionId})
```

## Key Files

| File | Purpose |
|------|---------|
| `bot.py` | Slack Socket Mode harness — routing, throttling, banning, thread management |
| `app/CustomerSupport/main.py` | AgentCore entrypoint — agent construction, gate eval, multimodal |
| `app/CustomerSupport/memory/session.py` | Memory session manager factory (auto-coupled today) |
| `app/CustomerSupport/mcp_client/client.py` | MCP client factories (Exa web search, Gateway) |
| `app/CustomerSupport/model/load.py` | Model loader |
| `agentcore/agentcore.json` | Infrastructure-as-code: runtime, memory, gateway config |
| `generate_curl.py` | Dev utility — fetches M2M token and prints a curl command |
| `.env` | Secrets: COGNITO_CLIENT_ID, COGNITO_CLIENT_SECRET, COGNITO_DOMAIN, SLACK_* tokens |
| `env_example.txt` | Template for .env (no secrets) |

## Auth Model

Two flows, same CUSTOM_JWT authorizer on the runtime:

- **User-based** (`USER_PASSWORD_AUTH`): interactive/console testing with Cognito user
- **M2M** (`client_credentials`): bot.py uses client ID + secret → Bearer token

The M2M token has no `username` claim. `extract_user_id()` in main.py falls back to
the `x-amzn-bedrock-agentcore-runtime-custom-user-id` header for actor identity.

## Session & Identity Mapping (current)

- **session_id**: `slack_{sha256(channel_id:thread_ts)}` — one AgentCore session per Slack thread
- **user_id** (actor): currently set to `channel_id` by bot.py via custom header
- **Memory namespaces**: `/users/{channel_id}/facts` and `/summaries/{channel_id}/{session_id}`

This means memory is currently channel-scoped, not user-scoped, and is auto-coupled to every invocation.

## Conventions

- Python throughout (no TypeScript agent code)
- Strands Agent framework
- `agentcore/agentcore.json` is the source of truth for infrastructure — never edit generated CDK
- Secrets go in `.env` (gitignored), templates in `env_example.txt`
- `agentcore validate` before `agentcore deploy -y -v`
- Commit messages: short imperative summary, then detail paragraph

## Known Constraints

- This environment does NOT have direct Bedrock API access — all model calls go through AgentCore Runtime
- Gateway auth is currently NONE (not yet secured with JWT like the runtime)
- The Exa MCP endpoint (`mcp.exa.ai/mcp`) may not be provisioned for this workshop
- AgentCore Memory auto-couples to every invocation via session_manager — there is no explicit memory tool yet

## What's Next (OpenSpec proposal in progress)

Rearchitecting memory and Slack integration:
1. Make ambient gate help-biased, enriched with Slack thread/user context
2. Decouple AgentCore Memory from automatic invocation — make it explicit tools
3. Memory is channel-scoped only (channel ID as the sole memory namespace key — no user-level memory)
4. Nested agent architecture with restricted tool profiles for memory operations
5. Slack thread context becomes the primary conversational state (not memory)
