# CustomerSupport Agent

A customer support agent deployed on Amazon Bedrock AgentCore, fronted by a Slack bot using Socket Mode.

## Architecture

```
Slack (Socket Mode)
  └─ bot.py (harness)
       ├─ Gate evaluation   → POST /invocations { action: "evaluate_gate", prompt: ... }
       ├─ Normal invocation → POST /invocations { prompt: ..., image_b64?: ... }
       └─ Auth: Cognito M2M OAuth (client_credentials grant)

AgentCore Runtime (CUSTOM_JWT auth)
  └─ app/CustomerSupport/main.py
       ├─ Tools: get_return_policy, get_product_info, MCP clients
       ├─ Memory: AgentCore Memory (semantic + summarization)
       └─ Model: Bedrock via AgentCore Runtime
```

The harness (`bot.py`) runs locally or in Docker. The agent (`app/CustomerSupport/`) is deployed to AgentCore Runtime via `agentcore deploy`. See [CLAUDE.md](CLAUDE.md) for full architecture details.

## Prerequisites

- **make** (`sudo yum install -y make` on Amazon Linux, `sudo apt-get install -y make` on Debian/Ubuntu)
- **Python 3.14+**
- **Docker** (optional, for containerized harness)
- **AWS CLI** configured with credentials (`aws configure` or ambient IAM role)
- **AgentCore CLI** (`npm install -g @aws/agentcore-cli`)
- **Slack app tokens** (see [Obtaining Slack Tokens](#obtaining-slack-tokens) below)

## Getting Started

### 1. Initialize configuration

```bash
make init
```

This copies `.env.example` to `.env`. Fill in the required values before continuing.

### 2. Set up Cognito M2M credentials

Add your `COGNITO_USER_POOL_ID` and `COGNITO_DOMAIN` to `.env`, then:

```bash
make setup-cognito
```

This creates a Cognito app client and writes `COGNITO_CLIENT_ID` and `COGNITO_CLIENT_SECRET` into `.env`.

### 3. Install harness dependencies

```bash
make install
```

### 4. Deploy the agent

```bash
make deploy
```

`AGENT_URL` is set automatically by `make setup`. If deploying separately, run `make setup-agent-url` to update it.

### 5. Configure Slack and run the harness

Follow [docs/slack-setup.md](docs/slack-setup.md) to create your Slack app with the required scopes and tokens, then add `SLACK_APP_TOKEN` and `SLACK_BOT_TOKEN` to `.env`.

```bash
make run-harness
```

### Testing without Slack

```bash
make curl-test
```

This generates and prints a curl command using your M2M credentials.

## Docker

Run the harness in a container instead of a local venv:

```bash
make up       # build and start in background
make down     # stop
```

The container reads `.env` for configuration and mounts `~/.aws/` read-only for AWS credentials.

## Slack App Setup

See [docs/slack-setup.md](docs/slack-setup.md) for the complete guide with screenshots — creating the app, adding all required OAuth scopes, generating tokens, and enabling event subscriptions.

## Makefile Targets

| Target | Description |
|---|---|
| `make setup` | One-shot: init + auto-discover all AWS config (Cognito + agent URL) |
| `make init` | Bootstrap `.env` from template |
| `make setup-cognito` | Auto-discover Cognito pool, domain, and M2M client from AWS |
| `make setup-agent-url` | Auto-discover deployed agent URL from AgentCore |
| `make install` | Create venv and install harness dependencies |
| `make deploy` | Deploy agent to AgentCore (`~3 min`) |
| `make run-harness` | Start the Slack bot locally |
| `make curl-test` | Generate a test curl command with M2M auth |
| `make up` | Build and start harness via Docker Compose |
| `make down` | Stop the Docker Compose harness |
