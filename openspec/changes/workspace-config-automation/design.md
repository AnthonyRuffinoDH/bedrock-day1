# Design

## Context

The project has two dependency scopes with different packaging:

- **Agent-side** (`app/CustomerSupport/`): has `pyproject.toml` with pinned deps (`bedrock-agentcore`, `strands-agents`, `mcp`, `pyjwt`, `botocore`, `aws-opentelemetry-distro`). Deployed via `agentcore deploy`.
- **Harness-side** (`bot.py`): imports `slack_bolt`, `requests`, `python-dotenv` but has no dependency manifest. Run locally.

The env template (`env_example.txt`) uses `export` prefixes, which is non-standard for `python-dotenv`. `generate_curl.py` has its own `load_env()` function instead of using `python-dotenv`, and hardcodes an ARN on line 28 instead of reading `AGENT_URL` from env.

See proposal.md for full motivation.

## Goals / Non-Goals

**Goals:**
- Single `make init && make install && make run-harness` path from clone to running
- Complete `.env.example` as the single source of truth for required configuration
- `generate_curl.py` works from `.env` with no hardcoded values
- Project-specific README that covers architecture and setup

**Non-Goals:**
- CI/CD pipeline (no GitHub Actions or similar)
- Containerizing the AgentCore agent (deployed separately via `agentcore deploy`)
- Changing any agent behavior or runtime code (`bot.py` logic, `main.py`, memory, tools)
- Automating Slack app creation (tokens require manual Slack UI steps)
- Managing agent-side deps (already handled by `pyproject.toml` and `agentcore deploy`)

## Decisions

### D1: Rename `env_example.txt` → `.env.example`

**Choice:** Standard `.env.example` filename, drop `export` prefixes, add section comments and defaults.

**Why:** `.env.example` is the universal convention recognized by `python-dotenv`, VS Code extensions, and most frameworks. The current `export` prefix is unnecessary — `python-dotenv` parses with or without it, but dropping it keeps the file compatible with `docker run --env-file` and similar tools.

**Alternative:** Keep `env_example.txt` alongside a new `.env.example`. Rejected — maintaining two templates invites drift.

### D2: Harness-only `requirements.txt` at project root

**Choice:** Create `requirements.txt` at the project root covering only harness dependencies (`slack_bolt`, `requests`, `python-dotenv`). Pin to compatible ranges (e.g., `slack_bolt>=1.18.0`).

**Why:** Agent-side deps are already in `app/CustomerSupport/pyproject.toml` and are installed by `agentcore deploy` into its own environment. The root `requirements.txt` serves `make install` for the local harness only.

**Alternative:** A root `pyproject.toml` with a `[harness]` extra. Rejected — overkill for three dependencies, and `pip install -r requirements.txt` is simpler in a Makefile.

### D3: Makefile targets

**Choice:** Eight targets with a `.venv` convention:

| Target | What it does |
|---|---|
| `init` | Copies `.env.example` → `.env` if missing, prints reminder to fill in secrets |
| `setup-cognito` | Requires `COGNITO_USER_POOL_ID` in `.env`. Calls `aws cognito-idp create-user-pool-client` to create an M2M app client with `client_credentials` grant, parses JSON output, writes `COGNITO_CLIENT_ID` and `COGNITO_CLIENT_SECRET` back into `.env` |
| `install` | Creates `.venv/` via `python3 -m venv`, installs from `requirements.txt` |
| `deploy` | Runs `agentcore deploy -y -v` |
| `run-harness` | Activates `.venv/`, runs `python bot.py` |
| `curl-test` | Activates `.venv/`, runs `python generate_curl.py` |
| `up` | `docker compose up -d --build` — build and start harness in background |
| `down` | `docker compose down` — stop harness |

**Why `setup-cognito` requires `COGNITO_USER_POOL_ID`:** The pool already exists (created by the workshop). Auto-discovering it would require fragile name-based filtering. Having the user paste the pool ID once is more reliable.

**Alternative:** A single `setup` target that chains everything. Rejected — users need to run Cognito setup separately since it requires the pool ID first, and `deploy` is a distinct operation they may not want every time.

### D4: Fix `generate_curl.py` — use `python-dotenv` and `AGENT_URL`

**Choice:** Replace the custom `load_env()` function with `python-dotenv`'s `load_dotenv()` (already a project dependency). Replace the hardcoded ARN + URL construction with `os.getenv("AGENT_URL")`.

**Why:** The hardcoded ARN is account-specific and breaks for anyone else. `AGENT_URL` is already in the env template and contains the exact URL that the ARN-based construction produces.

### D5: Docker Compose for the harness

**Choice:** Add a `Dockerfile` and `docker-compose.yml` to run `bot.py` in a container. The Compose service:
- Uses `env_file: .env` to load all configuration
- Mounts `~/.aws/` read-only to forward ambient AWS credentials (needed for Cognito token exchange)
- Exposes no ports (Socket Mode is outbound-only)
- Runs `python bot.py` as the entrypoint

The `Dockerfile` is a slim Python image that copies `bot.py`, `generate_curl.py`, `requirements.txt`, and installs deps. It does NOT include `app/CustomerSupport/` — the agent is deployed separately to AgentCore.

**Why:** Docker Compose gives a one-command start (`docker compose up`) with no venv management, and makes it easy to run the harness in the background (`docker compose up -d`). The `.env` file works natively with Compose's `env_file` directive — no additional config mapping needed.

**Alternative:** Docker-only (no Compose). Rejected — Compose is simpler for single-service setups with env files and volume mounts, and leaves room to add services later (e.g., a local test endpoint).

**Makefile integration:** Two new targets:
| Target | What it does |
|---|---|
| `up` | `docker compose up -d --build` — build and start harness in background |
| `down` | `docker compose down` — stop harness |

### D6: `.venv/` instead of `.tmp_venv/`

**Choice:** Use `.venv/` as the virtualenv directory (standard convention). Update `.gitignore` to ignore `.venv/` and keep `.tmp_venv/` for backwards compatibility.

**Why:** `.venv/` is the default for `python -m venv .venv` and is recognized by VS Code, PyCharm, and other tooling automatically.

## Risks / Trade-offs

- **`make setup-cognito` writes to `.env`**: Uses `sed` to replace placeholder values in-place. If the user has added custom formatting or comments near those lines, `sed` could mangle them. → Mitigation: the Makefile targets only lines matching the exact placeholder pattern from `.env.example`.

- **`env_example.txt` removal breaks anyone referencing it**: → Mitigation: this is a workshop project with no downstream consumers. The rename is clean.

- **`requirements.txt` version drift from agent-side deps**: The harness and agent don't share a virtualenv, so this is a non-issue. They only share `python-dotenv`, which is stable.

- **No lockfile**: `requirements.txt` uses compatible ranges, not pinned hashes. → Acceptable for a workshop project. A `pip freeze` snapshot can be added later if needed.

- **AWS credentials in Docker**: The container mounts `~/.aws/` read-only for ambient credentials. This works in the workshop environment (IAM role via instance profile) but wouldn't work in environments using SSO or MFA-based credential chains. → Acceptable for the workshop. A future improvement could use `AWS_CONTAINER_CREDENTIALS_FULL_URI` for ECS-style credential forwarding.
