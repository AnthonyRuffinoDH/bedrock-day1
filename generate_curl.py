import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
import urllib.parse
import json

# Your specific deployment details
region = 'us-west-2'
service = 'bedrock-agentcore'
arn = 'arn:aws:bedrock-agentcore:us-west-2:008977808353:runtime/CustomerSupport_CustomerSupport-0O47OSHdWX'

# URL encode the ARN for the path
url_encoded_arn = urllib.parse.quote(arn, safe='')
url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{url_encoded_arn}/invocations"

# The payload you want to send
payload = json.dumps({"prompt": "What is the return policy for electronics?"})

# Get credentials from the workshop environment
session = boto3.Session()
credentials = session.get_credentials()

if not credentials:
    print("Could not find AWS credentials.")
    exit(1)

# Freeze credentials to extract the access key, secret key, and token securely
frozen_creds = credentials.get_frozen_credentials()

# Prepare the HTTP request
request = AWSRequest(method='POST', url=url, data=payload)
request.headers['Content-Type'] = 'application/json'
request = AWSRequest(method='POST', url=url, data=payload)
request.headers['Content-Type'] = 'application/json'
request.headers['x-amzn-bedrock-agentcore-runtime-custom-user-id'] = 'workshop-user-123'

# This adds the necessary 'Authorization', 'X-Amz-Date', and 'X-Amz-Security-Token' headers
SigV4Auth(frozen_creds, service, region).add_auth(request)

# Build the curl command string
curl_cmd = f"curl -s -X POST '{url}' \\\n"
for key, value in request.headers.items():
    curl_cmd += f"  -H '{key}: {value}' \\\n"
curl_cmd += f"  -d '{payload}'"

print("\n# Copy and paste this command into your terminal.")
print("# Note: AWS signatures expire in 5 minutes, so use it quickly!\n")
print(curl_cmd)
print("\n")