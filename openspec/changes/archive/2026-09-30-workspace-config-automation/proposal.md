# Proposal

## Why

The workspace grew organically during workshop prototyping and lacks the automation and documentation needed for repeatable setup. Onboarding requires tribal knowledge: there is no `Makefile`, no `requirements.txt` at the project root, the README is AgentCore CLI boilerplate, the env template (`env_example.txt`) is incomplete, and `generate_curl.py` has a hardcoded ARN. Before we give the agent tools to modify its own codebase, the workspace must be configuration-driven and self-documenting.

## What Changes

- **Complete the env template**: Rename `env_example.txt` to `.env.example` (standard convention), add missing variables (`COGNITO_USER_POOL_ID`, `THROTTLE_WINDOW_SEC`, `MAX_WARNINGS`, `WARNING_WINDOW_MIN`, `BAN_DURATION_MIN`, `THREAD_CONTEXT_LIMIT`) with placeholder values and comments
- **Create `Makefile`**: Targets for `init` (bootstrap `.env` from template), `setup-cognito` (automate M2M client creation via `aws cognito-idp` CLI), `install` (venv + deps), `deploy` (`agentcore deploy`), `run-harness` (start `bot.py`), `curl-test` (generate and run a test curl)
- **Create `requirements.txt`**: Capture current Python dependencies at the project root
- **Harden `.gitignore`**: Add `__pycache__/`, `*.pyc`, `.agentcore/`, `node_modules/`, `venv/`, `*.egg-info/`
- **Rewrite `README.md`**: Project-specific onboarding guide with architecture overview, step-by-step setup using Makefile targets, and manual Slack token instructions
- **Fix `generate_curl.py`**: Replace hardcoded ARN with `AGENT_URL` from `.env`
- **Add `docker-compose.yml`**: Run the Slack harness (`bot.py`) via `docker compose up`, with `.env` auto-loaded, AWS credentials forwarded from the host, and a `Dockerfile` for the harness image
- **Add Makefile `up`/`down` targets**: Wrap `docker compose up -d` and `docker compose down` for convenience

**Not changing** (contrary to Gemini's suggestion): `bot.py` and `main.py` already load all configuration via `os.getenv()` with sensible defaults. No refactoring needed there.

## Capabilities

### New Capabilities

None. This change introduces no new agent behavior or spec-level capabilities.

### Modified Capabilities

None. Existing specs (`agent-tool-profiles`, `explicit-agent-memory`, `slack-conversation-context`) are unaffected — this is pure tooling, configuration, and documentation.

This change sets `skip_specs: true` because no behavior changes.

## Impact

- **Files created**: `Makefile`, `requirements.txt`, `.env.example`, `Dockerfile`, `docker-compose.yml`
- **Files modified**: `.gitignore`, `README.md`, `generate_curl.py`
- **Files removed**: `env_example.txt` (replaced by `.env.example`)
- **No code behavior changes**: `bot.py`, `main.py`, and all agent code remain untouched
- **No infrastructure changes**: `agentcore/agentcore.json` unchanged, no deploy needed
- **Risk**: Low — no runtime code is modified. The `generate_curl.py` change requires the user to have `AGENT_URL` in `.env` (already documented in the existing template).
