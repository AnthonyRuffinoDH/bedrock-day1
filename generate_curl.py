import os
import urllib.request
import urllib.parse
import json
import base64

from dotenv import load_dotenv

load_dotenv()

client_id = os.environ["COGNITO_CLIENT_ID"]
client_secret = os.environ["COGNITO_CLIENT_SECRET"]
cognito_domain = os.environ["COGNITO_DOMAIN"]
agent_url = os.environ["AGENT_URL"]
payload = json.dumps({"prompt": "What is the return policy for electronics?"})

print("Fetching Machine-to-Machine Token from Cognito...")

token_url = f"{cognito_domain}/oauth2/token"
auth_b64 = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

headers = {
    "Authorization": f"Basic {auth_b64}",
    "Content-Type": "application/x-www-form-urlencoded",
}
data = urllib.parse.urlencode(
    {"grant_type": "client_credentials", "scope": "agent-api/invoke"}
).encode()

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

curl_cmd = f"curl -s -X POST '{agent_url}' \\\n"
curl_cmd += f"  -H 'Content-Type: application/json' \\\n"
curl_cmd += f"  -H 'x-amzn-bedrock-agentcore-runtime-custom-user-id: slackbot-123' \\\n"
curl_cmd += f"  -H 'Authorization: Bearer {access_token}' \\\n"
curl_cmd += f"  -d '{payload}'"

print("# Run this command:")
print(curl_cmd)
print("\n")
