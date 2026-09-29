---
title: "Lab 4.2: Adding Machine-to-Machine (M2M) OAuth Authentication"
weight: 53
---

**⏱️ Estimated time: ~10 minutes**

## Overview

In Lab 4, you secured your agent with Cognito JWT authentication using a **user-based flow** — a human signs in with a username and password, and the app obtains a token on their behalf. That works for interactive applications, but what about a **backend service** like a Slack bot, a cron job, or another agent that needs to call your API with no human in the loop?

For that, you need the **OAuth 2.0 Client Credentials Grant**, also known as Machine-to-Machine (M2M) authentication. Instead of a username and password, the caller authenticates with a **client ID and client secret** — standard OAuth, no AWS IAM credentials required.

In this lab, you'll:

- **Create a Cognito Resource Server** with a custom scope for your agent API
- **Create an M2M App Client** configured for the `client_credentials` grant
- **Register the new client** in your AgentCore runtime's allowed clients list
- **Update `generate_curl.py`** to obtain a Bearer token via the M2M flow and call the agent — no `boto3` or SigV4 signing needed
- **Externalize credentials** into a `.env` file (gitignored) instead of hardcoding them

### What's already running

| Resource | Status | Created In |
|----------|--------|------------|
| AgentCore Runtime (CUSTOM_JWT auth) | ✅ READY | Lab 4 |
| Cognito User Pool + Domain | ✅ Active | Prerequisites / Lab 4 |
| AgentCore Memory | ✅ Deployed | Lab 2 |
| AgentCore Gateway | ✅ Deployed | Lab 3 |

### What you'll add

| Resource | Purpose |
|----------|---------|
| Cognito Resource Server (`agent-api`) | Defines the `agent-api/invoke` scope |
| M2M App Client (`SlackBotM2M`) | Client ID + secret for the `client_credentials` grant |
| Updated `allowedClients` in `agentcore.json` | Authorizes the new M2M client to call the runtime |

## Step 1: Create the Cognito Resource Server

A **Resource Server** defines custom OAuth scopes. You'll create one called `agent-api` with a single scope `invoke`, so M2M clients can request `agent-api/invoke` when fetching a token.

Your Cognito User Pool already has a domain from Lab 4 prerequisites. Retrieve it first:

:::code{language=bash}
USER_POOL_ID=$(aws ssm get-parameter \
  --name /app/customersupport/agentcore/pool_id \
  --query 'Parameter.Value' --output text)

COGNITO_DOMAIN_PREFIX=$(aws cognito-idp describe-user-pool \
  --user-pool-id $USER_POOL_ID \
  --query 'UserPool.Domain' --output text)

REGION=$(aws configure get region)

echo "User Pool ID:   $USER_POOL_ID"
echo "Cognito Domain: https://${COGNITO_DOMAIN_PREFIX}.auth.${REGION}.amazoncognito.com"
:::

Now create the Resource Server:

:::code{language=bash}
aws cognito-idp create-resource-server \
    --user-pool-id $USER_POOL_ID \
    --identifier "agent-api" \
    --name "Agent API" \
    --scopes ScopeName="invoke",ScopeDescription="Invoke Agent"
:::

You should see output confirming the resource server was created with the `invoke` scope:

:::code{language=bash showCopyAction=false}
{
    "ResourceServer": {
        "UserPoolId": "us-west-2_...",
        "Identifier": "agent-api",
        "Name": "Agent API",
        "Scopes": [
            {
                "ScopeName": "invoke",
                "ScopeDescription": "Invoke Agent"
            }
        ]
    }
}
:::

## Step 2: Create the M2M App Client

Create a new Cognito App Client that uses the `client_credentials` flow. This client has a generated secret (no user interaction needed) and is restricted to the `agent-api/invoke` scope:

:::code{language=bash}
aws cognito-idp create-user-pool-client \
    --user-pool-id $USER_POOL_ID \
    --client-name "SlackBotM2M" \
    --generate-secret \
    --allowed-o-auth-flows "client_credentials" \
    --allowed-o-auth-scopes "agent-api/invoke" \
    --allowed-o-auth-flows-user-pool-client \
    --query '{ClientId:ClientId, ClientSecret:ClientSecret}' \
    --output table
:::

**Save the output** — you'll need the `ClientId` and `ClientSecret` in the next steps:

:::code{language=bash showCopyAction=false}
---------------------------------------------
|         CreateUserPoolClient              |
+---------------+---------------------------+
|   ClientId    |  abc123def456...          |
|   ClientSecret|  xyz789secret...          |
+---------------+---------------------------+
:::

Store them in shell variables for the remaining steps:

:::code{language=bash}
M2M_CLIENT_ID="<your ClientId from above>"
M2M_CLIENT_SECRET="<your ClientSecret from above>"
:::

:::alert{type="warning" header="Save these credentials securely"}
The `ClientSecret` is only shown once at creation time. If you lose it, you'll need to create a new app client. Never commit these values to source control.
:::

## Step 3: Register the M2M Client in AgentCore

Your runtime's `CUSTOM_JWT` authorizer only accepts tokens from clients listed in `allowedClients`. Add the new M2M client ID alongside the existing ones.

Open `agentcore/agentcore.json` in the Code Editor and add the M2M client ID to the `allowedClients` array:

:::code{language=json showCopyAction=false}
"authorizerConfiguration": {
  "customJwtAuthorizer": {
    "discoveryUrl": "https://cognito-idp.<region>.amazonaws.com/<pool-id>/.well-known/openid-configuration",
    "allowedClients": [
      "<existing-client-id>",
      "<existing-web-client-id>",
      "<your-new-M2M-client-id>"
    ]
  }
}
:::

The existing client IDs from Lab 4 stay — they're used for the user-based flow. You're just adding the M2M client alongside them.

### Validate and deploy

:::code{language=bash}
agentcore validate
:::

:::code{language=bash}
agentcore deploy -y -v
:::

Wait for deployment to complete. The only change is the updated `allowedClients` list — the runtime itself doesn't need repackaging.

## Step 4: Create the `.env` File

Instead of hardcoding credentials in your scripts, store them in a `.env` file that is excluded from version control.

Create `.env` in your project root:

:::code{language=bash}
cat > .env << 'EOF'
export COGNITO_CLIENT_ID="<your M2M ClientId>"
export COGNITO_CLIENT_SECRET="<your M2M ClientSecret>"
export COGNITO_DOMAIN="https://<your-domain-prefix>.auth.<region>.amazoncognito.com"
EOF
:::

Replace the placeholder values with the actual credentials from Step 2 and the domain from Step 1. For example:

:::code{language=bash showCopyAction=false}
export COGNITO_CLIENT_ID="<ClientId from Step 2>"
export COGNITO_CLIENT_SECRET="<ClientSecret from Step 2>"
export COGNITO_DOMAIN="https://<domain-prefix>.auth.<region>.amazoncognito.com"
:::

### Add `.env` to `.gitignore`

Make sure credentials never get committed:

:::code{language=bash}
echo '.env' >> .gitignore
:::

:::alert{type="info" header="What about the old AWS credentials and Cognito user-flow variables?"}
If your `.env` previously contained `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, or the user-based Cognito variables (`CLIENT_ID`, `POOL_ID`, `WEB_POOL_CLIENT_ID`, `DISCOVERY_URL`), you can remove them. The M2M flow in `generate_curl.py` only needs the three `COGNITO_*` variables above — no AWS IAM credentials, no user pool details. Keep the old variables only if other scripts still depend on them.
:::

## Step 5: Update `generate_curl.py` for M2M

The old `generate_curl.py` used `boto3` and AWS SigV4 signing — it needed AWS IAM credentials and generated short-lived signed requests. With M2M OAuth, we use standard HTTP to get a Bearer token from Cognito's `/oauth2/token` endpoint. No `boto3` required.

Replace `generate_curl.py` with:

:::code{language=python}
import os
import urllib.request
import urllib.parse
import json
import base64


def load_env(path=".env"):
    """Load environment variables from a shell-style .env file."""
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, _, value = line.partition("=")
            value = value.strip("\"'")
            os.environ[key] = value


load_env()

client_id = os.environ["COGNITO_CLIENT_ID"]
client_secret = os.environ["COGNITO_CLIENT_SECRET"]
cognito_domain = os.environ["COGNITO_DOMAIN"]

# Your agent's runtime invocation URL
arn = "arn:aws:bedrock-agentcore:us-west-2:008977808353:runtime/CustomerSupport_CustomerSupport-0O47OSHdWX"
url_encoded_arn = urllib.parse.quote(arn, safe="")
agent_url = f"https://bedrock-agentcore.us-west-2.amazonaws.com/runtimes/{url_encoded_arn}/invocations"
payload = json.dumps({"prompt": "What is the return policy for electronics?"})

print("Fetching Machine-to-Machine Token from Cognito...")

# 1. Build the OAuth2 Client Credentials request
token_url = f"{cognito_domain}/oauth2/token"
auth_b64 = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

headers = {
    "Authorization": f"Basic {auth_b64}",
    "Content-Type": "application/x-www-form-urlencoded",
}
data = urllib.parse.urlencode(
    {"grant_type": "client_credentials", "scope": "agent-api/invoke"}
).encode()

# 2. Get the token
try:
    req = urllib.request.Request(token_url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req) as response:
        response_data = json.loads(response.read().decode())
        access_token = response_data["access_token"]
        print("Success! Token retrieved.\n")
except urllib.error.URLError as e:
    print(f"Failed to get token: {e}")
    if hasattr(e, "read"):
        print(e.read().decode())
    exit(1)

# 3. Build the curl command with the Bearer token
curl_cmd = f"curl -s -X POST '{agent_url}' \\\n"
curl_cmd += f"  -H 'Content-Type: application/json' \\\n"
curl_cmd += f"  -H 'x-amzn-bedrock-agentcore-runtime-custom-user-id: slackbot-123' \\\n"
curl_cmd += f"  -H 'Authorization: Bearer {access_token}' \\\n"
curl_cmd += f"  -d '{payload}'"

print("# Run this command:")
print(curl_cmd)
print("\n")
:::

:::alert{type="info" header="What changed from the SigV4 version"}
| Before (Lab 4) | After (Lab 4.2) |
|---|---|
| Used `boto3` + `SigV4Auth` to sign requests | Uses standard OAuth2 `client_credentials` grant |
| Required AWS IAM credentials (`AWS_ACCESS_KEY_ID`, etc.) | Requires only `COGNITO_CLIENT_ID` + `COGNITO_CLIENT_SECRET` |
| Signatures expired in 5 minutes | Bearer tokens last 60 minutes (configurable) |
| Credentials sourced from AWS session | Credentials sourced from `.env` file |
| Only Python standard library needed | Only Python standard library needed (no `boto3`) |
:::

## Step 6: Test the M2M Flow

Run the updated script:

:::code{language=bash}
python3 generate_curl.py
:::

You should see:

:::code{language=bash showCopyAction=false}
Fetching Machine-to-Machine Token from Cognito...
Success! Token retrieved.

# Run this command:
curl -s -X POST 'https://bedrock-agentcore.us-west-2.amazonaws.com/runtimes/...' \
  -H 'Content-Type: application/json' \
  -H 'x-amzn-bedrock-agentcore-runtime-custom-user-id: slackbot-123' \
  -H 'Authorization: Bearer eyJra...' \
  -d '{"prompt": "What is the return policy for electronics?"}'
:::

Copy and run the generated curl command. You should get a streamed response from your agent with the return policy details.

### Verify unauthenticated requests are still rejected

Try calling the endpoint without a token to confirm the auth is enforced:

:::code{language=bash}
curl -s -X POST "https://bedrock-agentcore.us-west-2.amazonaws.com/runtimes/$(python3 -c 'import urllib.parse; print(urllib.parse.quote("arn:aws:bedrock-agentcore:us-west-2:008977808353:runtime/CustomerSupport_CustomerSupport-0O47OSHdWX", safe=""))')/invocations" \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "Hello"}'
:::

You should see an authentication error — the M2M token is required.

## How It Works

Here's what happens when a backend service (e.g., a Slack bot) calls your agent using the M2M flow:

```
┌──────────────┐     ① client_credentials grant      ┌──────────────────┐
│              │ ──────────────────────────────────▶  │                  │
│  Slack Bot   │     (client ID + secret)             │  Cognito         │
│  (M2M Client)│ ◀──────────────────────────────────  │  /oauth2/token   │
│              │     ② access_token (JWT)              │                  │
└──────┬───────┘                                      └──────────────────┘
       │
       │  ③ POST /invocations
       │     Authorization: Bearer <token>
       ▼
┌──────────────────┐     ④ Validate JWT          ┌──────────────────┐
│  AgentCore       │ ────────────────────────▶   │  Cognito JWKS    │
│  Runtime         │     (check signature,       │  (signing keys)  │
│  (CUSTOM_JWT)    │      issuer, client_id)     └──────────────────┘
│                  │
│  ⑤ Invoke Agent  │
└──────────────────┘
```

1. The M2M client sends its `client_id` and `client_secret` to Cognito's token endpoint
2. Cognito validates the credentials and returns a JWT access token with the `agent-api/invoke` scope
3. The client calls the AgentCore Runtime with the token as a Bearer header
4. AgentCore validates the JWT using Cognito's JWKS (signing keys), checking the signature, issuer, expiry, and that the `client_id` is in the `allowedClients` list
5. If valid, the agent is invoked

:::alert{type="info" header="M2M tokens don't have a username claim"}
Unlike user-based tokens (from `USER_PASSWORD_AUTH`), M2M tokens issued via `client_credentials` don't contain a `username` claim — there's no human user involved. The `extract_user_id()` function in `main.py` falls back to the `x-amzn-bedrock-agentcore-runtime-custom-user-id` header in this case. That's why the curl command includes this header — it tells the agent which user identity to associate with the session for memory purposes.
:::

## What Just Happened?

You added an M2M authentication path to your agent without changing the runtime's auth model — the same `CUSTOM_JWT` authorizer that validates user tokens also validates M2M tokens. The only infrastructure change was adding the new client ID to `allowedClients`. You also replaced the SigV4-based `generate_curl.py` with a cleaner OAuth2 version that needs no AWS credentials, and moved secrets into a gitignored `.env` file.

Your agent now supports **two authentication flows**:

| Flow | Use Case | How Token Is Obtained |
|------|----------|----------------------|
| User-based (`USER_PASSWORD_AUTH`) | Interactive apps, console testing | User signs in with email + password |
| M2M (`client_credentials`) | Slack bots, cron jobs, backend services | Service authenticates with client ID + secret |

Both produce JWT tokens that the same `CUSTOM_JWT` authorizer validates.

## Congratulations! ✅

Your agent can now be called by both human users and backend services, each with their own authentication flow, all validated by the same Cognito authorizer.

### What's Next

With M2M auth in place, you're ready to integrate your agent into a backend service like a Slack bot — the bot authenticates with its client credentials, obtains a token, and calls the agent on behalf of users.
