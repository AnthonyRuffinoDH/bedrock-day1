# herocore-bridge

A Python library that encapsulates the stateful WebSocket connection layer
between the **Slack harness** (`bot.py`) and the **AgentCore cloud runtime**
(`main.py`). It is the foundational primitive that makes it possible for any
team at Delivery Hero to build a Slack-connected AI agent with full
harness-side tool proxying — without reimplementing the protocol from scratch.

---

## What this library owns

| Module | Responsibility |
|---|---|
| `config.py` | Load `mcp_tools.json`, build the tool routing table |
| `auth.py` | Resolve `${ENV_VAR}` refs, build `Bearer`/`Basic`/raw auth headers |
| `executor.py` | Execute MCP tool calls via stdio subprocess or HTTP Streamable |
| `harness_client.py` | WebSocket client loop: send payload, receive stream, dispatch `tool_call` / `tool_result` / `text_delta` / `done` |
| `agent_proxy.py` | `WebSocketProxyTool` (AgentTool subclass) + `build_client_tool_wrappers` for the AgentCore side |

---

## Installation

### Direct from GitHub (no registry needed)

```bash
pip install "git+https://github.com/AnthonyRuffinoDH/bedrock-day1.git#subdirectory=packages/herocore-bridge"
```

With AgentCore extras (needed in the cloud app):

```bash
pip install "herocore-bridge[agentcore] @ git+https://github.com/AnthonyRuffinoDH/bedrock-day1.git#subdirectory=packages/herocore-bridge"
```

### From GitHub Packages (medium-term)

Once published to the GitHub Packages PyPI feed:

```bash
pip install herocore-bridge \
  --index-url https://pypi.pkg.github.com/AnthonyRuffinoDH \
  --extra-index-url https://pypi.org/simple
```

### From AWS CodeArtifact (enterprise)

```bash
pip install herocore-bridge \
  --index-url https://<domain>.d.codeartifact.<region>.amazonaws.com/pypi/<repo>/simple/
```

---

## Usage

### Harness side (`bot.py`)

```python
from herocore_bridge import load_mcp_tools, invoke_agentcore_ws

# Load tool definitions from mcp_tools.json
client_tools = load_mcp_tools()

# Call AgentCore over WebSocket (tools are injected automatically)
response = invoke_agentcore_ws(
    payload={"prompt": "Hello"},
    channel_id="C0C57H...",
    thread_ts="1234567890.000100",
    cognito_token_fn=get_cognito_token,       # callable that returns a fresh Bearer token
    agent_url=os.getenv("AGENT_URL"),
    client_tools=client_tools,
)
```

### AgentCore side (`main.py`)

```python
from herocore_bridge import build_client_tool_wrappers

# Inside ws_invoke handler:
client_tool_schemas = payload.get("client_tools", [])
extra_tools, _ = build_client_tool_wrappers(client_tool_schemas, websocket)
all_tools = cloud_tools + extra_tools
```

---

## Distribution roadmap

| Stage | Mechanism |
|---|---|
| **Now** | `pip install git+https://github.com/...#subdirectory=packages/herocore-bridge` |
| **Short-term** | GitHub Packages — private PyPI feed per org |
| **Enterprise** | AWS CodeArtifact domain (same AWS account as Bedrock) |

---

## Development

```bash
cd packages/herocore-bridge
pip install -e ".[agentcore]"
```

Build a wheel:

```bash
pip install hatch
hatch build
```
