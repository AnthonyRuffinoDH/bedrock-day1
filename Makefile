.PHONY: init install setup-cognito setup-agent-url deploy run-harness curl-test up down setup

init:
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "Created .env from .env.example"; \
	else \
		echo ".env already exists, skipping copy"; \
	fi

# Auto-discover all Cognito config from the workshop AWS environment.
# Finds pool ID via SSM, derives domain, reuses existing M2M client or creates one.
setup-cognito:
	@echo "Discovering Cognito configuration from AWS..."; \
	POOL_ID=$$(aws ssm get-parameter \
		--name /app/customersupport/agentcore/pool_id \
		--query 'Parameter.Value' --output text 2>/dev/null); \
	if [ -z "$$POOL_ID" ]; then \
		echo "ERROR: Could not find pool ID in SSM (/app/customersupport/agentcore/pool_id)"; \
		exit 1; \
	fi; \
	echo "  Pool ID: $$POOL_ID"; \
	sed -i "s|^COGNITO_USER_POOL_ID=.*|COGNITO_USER_POOL_ID=$$POOL_ID|" .env; \
	\
	DOMAIN_PREFIX=$$(aws cognito-idp describe-user-pool \
		--user-pool-id "$$POOL_ID" \
		--query 'UserPool.Domain' --output text); \
	REGION=$$(aws configure get region 2>/dev/null || echo "us-west-2"); \
	COGNITO_DOMAIN="https://$${DOMAIN_PREFIX}.auth.$${REGION}.amazoncognito.com"; \
	echo "  Domain:  $$COGNITO_DOMAIN"; \
	sed -i "s|^COGNITO_DOMAIN=.*|COGNITO_DOMAIN=$$COGNITO_DOMAIN|" .env; \
	\
	echo "Looking for existing M2M client..."; \
	CLIENT_IDS=$$(aws cognito-idp list-user-pool-clients \
		--user-pool-id "$$POOL_ID" --max-results 20 \
		--query 'UserPoolClients[].ClientId' --output text); \
	FOUND_ID=""; FOUND_SECRET=""; \
	for CID in $$CLIENT_IDS; do \
		INFO=$$(aws cognito-idp describe-user-pool-client \
			--user-pool-id "$$POOL_ID" --client-id "$$CID" 2>/dev/null); \
		HAS_CC=$$(echo "$$INFO" | python3 -c "import sys,json; d=json.load(sys.stdin)['UserPoolClient']; print('yes' if 'client_credentials' in d.get('AllowedOAuthFlows',[]) and 'agent-api/invoke' in d.get('AllowedOAuthScopes',[]) else 'no')" 2>/dev/null); \
		if [ "$$HAS_CC" = "yes" ]; then \
			FOUND_ID="$$CID"; \
			FOUND_SECRET=$$(echo "$$INFO" | python3 -c "import sys,json; print(json.load(sys.stdin)['UserPoolClient'].get('ClientSecret',''))"); \
			echo "  Found existing M2M client: $$FOUND_ID"; \
			break; \
		fi; \
	done; \
	if [ -z "$$FOUND_ID" ]; then \
		echo "  No existing M2M client found, creating one..."; \
		RESULT=$$(aws cognito-idp create-user-pool-client \
			--user-pool-id "$$POOL_ID" \
			--client-name "customersupport-m2m" \
			--generate-secret \
			--allowed-o-auth-flows "client_credentials" \
			--allowed-o-auth-scopes "agent-api/invoke" \
			--allowed-o-auth-flows-user-pool-client); \
		FOUND_ID=$$(echo "$$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['UserPoolClient']['ClientId'])"); \
		FOUND_SECRET=$$(echo "$$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['UserPoolClient']['ClientSecret'])"); \
		echo "  Created M2M client: $$FOUND_ID"; \
	fi; \
	sed -i "s|^COGNITO_CLIENT_ID=.*|COGNITO_CLIENT_ID=$$FOUND_ID|" .env; \
	sed -i "s|^COGNITO_CLIENT_SECRET=.*|COGNITO_CLIENT_SECRET=$$FOUND_SECRET|" .env; \
	echo "Done — Cognito config written to .env"

# Auto-discover the agent runtime URL from the deployed ARN.
setup-agent-url:
	@echo "Discovering agent URL from AgentCore..."; \
	ARN=$$(agentcore status 2>/dev/null | tr -d '\n' | grep -oP 'arn:aws:bedrock-agentcore:[^)]+runtime/[^)]+' | head -1); \
	if [ -z "$$ARN" ]; then \
		echo "ERROR: Could not find agent ARN. Is the agent deployed?"; \
		exit 1; \
	fi; \
	ENCODED_ARN=$$(python3 -c "import urllib.parse; print(urllib.parse.quote('$$ARN', safe=''))"); \
	REGION=$$(echo "$$ARN" | cut -d: -f4); \
	URL="https://bedrock-agentcore.$${REGION}.amazonaws.com/runtimes/$${ENCODED_ARN}/invocations"; \
	sed -i "s|^AGENT_URL=.*|AGENT_URL=$$URL|" .env; \
	echo "  AGENT_URL=$$URL"; \
	echo "Done — agent URL written to .env"

# Full setup: init + discover all AWS config. Only Slack tokens need manual entry.
setup: init setup-cognito setup-agent-url
	@echo ""; \
	echo "========================================"; \
	echo "  AWS setup complete!"; \
	echo "========================================"; \
	echo ""; \
	echo "Remaining: configure your Slack app and add tokens to .env"; \
	echo ""; \
	echo "  Full guide: docs/slack-setup.md"; \
	echo ""; \
	echo "  Quick steps:"; \
	echo "    1. Go to https://api.slack.com/apps and select (or create) your app"; \
	echo "    2. OAuth & Permissions > Bot Token Scopes — add all required scopes:"; \
	echo "         app_mentions:read  channels:history  chat:write  commands"; \
	echo "         emoji:read  files:read  files:write  groups:history"; \
	echo "         incoming-webhook  links:read  reactions:read  reactions:write"; \
	echo "         users.profile:read  users:read  users:read.email"; \
	echo "    3. Install (or reinstall) the app to your workspace"; \
	echo "    4. Copy the Bot User OAuth Token (xoxb-...) → SLACK_BOT_TOKEN in .env"; \
	echo "    5. Basic Information > App-Level Tokens > Generate Token and Scopes"; \
	echo "         Add scopes: connections:write, authorizations:read, app_configurations:write"; \
	echo "    6. Copy the app-level token (xapp-...) → SLACK_APP_TOKEN in .env"; \
	echo ""; \
	echo "========================================"; \
	echo "  Next steps"; \
	echo "========================================"; \
	echo ""; \
	echo "  # Install harness dependencies"; \
	echo "  make install"; \
	echo ""; \
	echo "  # Deploy the agent to AgentCore (~3 min)"; \
	echo "  make deploy"; \
	echo ""; \
	echo "  # Run the Slack harness locally"; \
	echo "  make run-harness"; \
	echo ""; \
	echo "  # Or run via Docker instead"; \
	echo "  make up"; \
	echo ""

install:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt
	@if ! docker compose version >/dev/null 2>&1; then \
		echo "Installing docker compose plugin..."; \
		mkdir -p ~/.docker/cli-plugins; \
		ARCH=$$(uname -m); \
		curl -fsSL "https://github.com/docker/compose/releases/download/v2.32.4/docker-compose-linux-$$ARCH" \
			-o ~/.docker/cli-plugins/docker-compose; \
		chmod +x ~/.docker/cli-plugins/docker-compose; \
		echo "Installed: $$(docker compose version)"; \
	else \
		echo "docker compose already available: $$(docker compose version)"; \
	fi

deploy:
	agentcore deploy -y -v

run-harness:
	.venv/bin/python bot.py

curl-test:
	.venv/bin/python generate_curl.py

up:
	docker compose up -d --build
	@echo "Stack running. Logs: make logs"

down:
	docker compose down
	@echo "Stack stopped."

logs:
	docker compose logs -f
