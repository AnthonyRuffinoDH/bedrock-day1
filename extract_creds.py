import boto3

session = boto3.Session()
credentials = session.get_credentials()

if not credentials:
    print("Could not find AWS credentials.")
    exit(1)

frozen_creds = credentials.get_frozen_credentials()

print("\n# Copy the following lines and paste them into the terminal on your WORK MACHINE:")
print("# -------------------------------------------------------------------------------")
print(f"export AWS_ACCESS_KEY_ID='{frozen_creds.access_key}'")
print(f"export AWS_SECRET_ACCESS_KEY='{frozen_creds.secret_key}'")

if frozen_creds.token:
    print(f"export AWS_SESSION_TOKEN='{frozen_creds.token}'")

print("export AWS_DEFAULT_REGION='us-west-2'")
print("# -------------------------------------------------------------------------------\n")
print("Once you paste those into your work machine's terminal, any AWS SDK or CLI command")
print("you run from that terminal will authenticate as this workshop user.")