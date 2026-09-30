# Spec

## Purpose

Gives Herocore controlled GitHub repository access through client-side MCP tools — enough to inspect code, create branches, commit changes, and open pull requests — completing the first self-improvement loop where the agent proposes and implements changes that a human reviews before merging.

## Requirements

### Requirement: GitHub MCP server in local environment
The GitHub MCP server (`ghcr.io/github/github-mcp-server`) SHALL run as a Docker Compose service alongside the existing filesystem MCP server. The service SHALL be internal to the Compose network with no host port exposure. The harness SHALL address it via Docker DNS. The service SHALL start as part of the normal `make up` workflow.

#### Scenario: GitHub MCP starts with the stack
- **WHEN** a developer runs `make up` with a valid `.env` containing `GITHUB_TOKEN`
- **THEN** the `mcp-github` service SHALL start and be reachable from the harness container via the Compose network

#### Scenario: GitHub MCP not exposed to host
- **WHEN** the Docker Compose stack is running
- **THEN** the `mcp-github` service SHALL NOT have any published ports — it is accessible only from other Compose services

### Requirement: GitHub credential setup
The `GITHUB_TOKEN` variable SHALL be documented in `.env.example` with a comment explaining its purpose and minimum required scopes. The `make setup` target SHALL detect whether `GITHUB_TOKEN` is set and report its status to the developer. If the token is available in the shell environment, `make setup` MAY auto-populate it in `.env` consistent with the project's existing setup behavior.

#### Scenario: Setup reports missing GitHub token
- **WHEN** a developer runs `make setup` and `GITHUB_TOKEN` is not set in the environment or `.env`
- **THEN** the setup output SHALL clearly state that GitHub access requires a token and explain where to configure it

#### Scenario: Setup detects existing GitHub token
- **WHEN** a developer runs `make setup` and `GITHUB_TOKEN` is already set in `.env`
- **THEN** the setup output SHALL acknowledge that the GitHub token is configured

#### Scenario: Minimum token scopes documented
- **WHEN** a developer reads `.env.example`
- **THEN** the comments SHALL specify the minimum GitHub token scopes required: `repo` (for private repositories) or `public_repo` (for public-only), plus `read:org` if organization repositories are targeted

### Requirement: Focused GitHub tool surface
The `mcp_tools.json` manifest SHALL declare a curated set of GitHub MCP tools sufficient for repository inspection and the branch-to-PR workflow. The tool set SHALL include capabilities for: reading file contents, searching code, listing branches, creating branches, creating or updating files, listing commits, creating pull requests, and reading pull request details. Tool names and schemas SHALL match the canonical GitHub MCP server output.

#### Scenario: All required tools declared
- **WHEN** the harness loads `mcp_tools.json`
- **THEN** the GitHub server entry SHALL declare tools covering: file reading, code search, branch listing, branch creation, file creation/update, commit listing, PR creation, and PR reading

#### Scenario: Tool schemas match upstream
- **WHEN** the harness injects GitHub tool schemas into the agent payload
- **THEN** the tool names, parameter names, and types SHALL match those returned by the GitHub MCP server's `tools/list` endpoint

### Requirement: Agent GitHub workflow guidance
The remote agent's system prompt SHALL include guidance for using GitHub client tools. The guidance SHALL distinguish GitHub tools (write-capable, repository-scoped, client-side) from filesystem tools (read-only, local workspace, agent-side). The guidance SHALL describe the expected workflow: explore code, create a branch, make changes, open a PR, and stop. The guidance SHALL explicitly prohibit merging PRs or initiating deployments.

#### Scenario: Agent understands GitHub workflow boundary
- **WHEN** the agent receives a request to make a code change
- **THEN** the agent SHALL follow the workflow of exploring, branching, committing, and opening a PR — and SHALL NOT attempt to merge the PR or trigger deployment

#### Scenario: Agent distinguishes tool sources
- **WHEN** the agent has both filesystem tools (agent-side) and GitHub tools (client-side) available
- **THEN** the agent SHALL use filesystem tools for fast local exploration and GitHub tools for authoritative repository operations and write actions

### Requirement: PR-bounded self-improvement
The GitHub tool surface SHALL NOT include tools for merging pull requests, deleting branches, managing releases, administering repository settings, or triggering CI/CD pipelines. The agent's write boundary SHALL end at opening a pull request.

#### Scenario: No merge capability
- **WHEN** the agent attempts to merge a pull request it created
- **THEN** no tool SHALL be available to perform that action — the agent SHALL report that a human must review and merge

#### Scenario: No deployment capability
- **WHEN** the agent attempts to trigger a deployment after opening a PR
- **THEN** no tool SHALL be available to perform that action

### Requirement: End-to-end validation path
After setup and startup, it SHALL be possible to validate the full integration path: harness authenticates to GitHub MCP, the agent can read repository contents through the MCP tool chain, and the agent can create a branch, commit a file change, and open a pull request — all via the standard MCP tool proxy without GitHub-specific harness code.

#### Scenario: Read validation
- **WHEN** a GitHub read tool (e.g., get file contents or list branches) is invoked through the harness
- **THEN** the tool call SHALL traverse the full path: harness → authenticated HTTP → GitHub MCP → GitHub API → result returned to agent

#### Scenario: Write validation (end-to-end)
- **WHEN** the agent receives a request to make a small code change
- **THEN** the agent SHALL create a branch, commit the change via GitHub MCP, open a PR, return the PR URL, and stop without merging
