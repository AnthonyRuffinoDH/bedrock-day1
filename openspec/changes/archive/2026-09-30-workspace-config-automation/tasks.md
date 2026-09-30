# Tasks

## 1. Environment template and gitignore

- [x] 1.1 Create `.env.example` with all variables organized by section (Slack, Cognito, AgentCore, Harness Tuning) with placeholder values and inline comments. Include `COGNITO_USER_POOL_ID`, `THROTTLE_WINDOW_SEC` (default 60), `MAX_WARNINGS` (default 3), `WARNING_WINDOW_MIN` (default 10), `BAN_DURATION_MIN` (default 60), `THREAD_CONTEXT_LIMIT` (default 20). Drop `export` prefixes. Verify: `python -c "from dotenv import dotenv_values; v = dotenv_values('.env.example'); assert 'COGNITO_USER_POOL_ID' in v and 'THROTTLE_WINDOW_SEC' in v"`
- [x] 1.2 Delete `env_example.txt`. Verify: `! test -f env_example.txt`
- [x] 1.3 Update `.gitignore` to add `__pycache__/`, `*.pyc`, `.venv/`, `node_modules/`, `*.egg-info/`, `.agentcore/` while keeping existing entries (`.env`, `.tmp_venv/`, `agentcore/.cli/`). Verify: `grep -q '__pycache__' .gitignore && grep -q '.venv/' .gitignore`

## 2. Harness dependencies

- [x] 2.1 Create `requirements.txt` at the project root with `slack_bolt>=1.18.0`, `requests>=2.31.0`, `python-dotenv>=1.0.0`. Verify: `pip install --dry-run -r requirements.txt` exits 0

## 3. Fix generate_curl.py

- [x] 3.1 Replace the custom `load_env()` function with `from dotenv import load_dotenv; load_dotenv()`. Replace the hardcoded ARN and URL construction with `os.getenv("AGENT_URL")`. Keep the rest of the auth flow (Cognito token fetch, curl generation) intact. Verify: copy `.env.example` to `.env.test`, fill in a dummy `AGENT_URL=https://example.com`, and confirm `python -c "from dotenv import load_dotenv; load_dotenv('.env.test'); import os; assert os.getenv('AGENT_URL') == 'https://example.com'"` passes; confirm no hardcoded ARN remains via `! grep -q 'arn:aws:bedrock-agentcore' generate_curl.py`

## 4. Docker Compose

- [x] 4.1 Create `Dockerfile` using `python:3.14-slim` base. Copy `bot.py`, `generate_curl.py`, `requirements.txt`, install deps via pip, set `CMD ["python", "bot.py"]`. Verify: `docker build -t customersupport-harness .` succeeds
- [x] 4.2 Create `docker-compose.yml` with a `harness` service that builds from the Dockerfile, uses `env_file: .env`, mounts `~/.aws:/root/.aws:ro` for credential forwarding, and sets `restart: unless-stopped`. Verify: `docker compose config` exits 0 (validates the compose file)
- [x] 4.3 Add `Dockerfile` patterns to `.gitignore` if needed (none expected — Dockerfile should be committed). Verify: `git ls-files --error-unmatch Dockerfile docker-compose.yml` shows both are trackable

## 5. Makefile

- [x] 5.1 Create `Makefile` with `init` target: copies `.env.example` → `.env` if `.env` does not exist, prints a reminder to fill in secrets. Verify: `rm -f .env && make init && test -f .env`
- [x] 5.2 Add `install` target: creates `.venv/` via `python3 -m venv .venv`, installs from `requirements.txt` into it. Verify: `make install && .venv/bin/pip list | grep slack`
- [x] 5.3 Add `setup-cognito` target: reads `COGNITO_USER_POOL_ID` from `.env`, calls `aws cognito-idp create-user-pool-client` with `--generate-secret`, `--allowed-o-auth-flows client_credentials`, `--allowed-o-auth-scopes agent-api/invoke`, parses `ClientId` and `ClientSecret` from JSON output, writes them back into `.env` via `sed`. Verify: inspect the target's commands with `make -n setup-cognito` and confirm it references `create-user-pool-client` and `sed`
- [x] 5.4 Add `deploy` target: runs `agentcore deploy -y -v`. Verify: `make -n deploy` shows the expected command. Note: actually running this target triggers an AgentCore deployment (~3 min)
- [x] 5.5 Add `run-harness` target: runs `bot.py` using `.venv/bin/python`. Verify: `make -n run-harness` shows `.venv/bin/python bot.py`
- [x] 5.6 Add `curl-test` target: runs `generate_curl.py` using `.venv/bin/python`. Verify: `make -n curl-test` shows `.venv/bin/python generate_curl.py`
- [x] 5.7 Add `up` target: runs `docker compose up -d --build`. Add `down` target: runs `docker compose down`. Verify: `make -n up` and `make -n down` show the expected docker compose commands

## 6. README and integration check

- [x] 6.1 Rewrite `README.md` with: project title and one-line description, architecture overview (Slack Socket Mode → bot.py harness → AgentCore Runtime), prerequisites (Python 3.14, Docker, AWS CLI, Slack app tokens), step-by-step Getting Started using Makefile targets (`make init`, `make setup-cognito`, `make install`, `make run-harness`), Docker alternative section (`make up` / `make down`), manual Slack token instructions (where to find them in the Slack API dashboard), and a link to `CLAUDE.md` for full architecture details. Verify: README contains headings for Architecture, Prerequisites, Getting Started, and Docker
- [x] 6.2 Run `make init && make install` end-to-end on a clean state (no `.env`, no `.venv/`) to verify the bootstrap path works. Verify: `.env` exists with all placeholder variables, `.venv/bin/python -c "import slack_bolt"` succeeds
