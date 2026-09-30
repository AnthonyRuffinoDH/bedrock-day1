import os
import json
import time
import base64
import hashlib
import logging
import threading
import requests
from urllib.parse import urlparse
from collections import defaultdict
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# Configure Detailed Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("SlackHarness")

load_dotenv()

app = App(token=os.getenv("SLACK_BOT_TOKEN"))

# Fetch Bot User ID
try:
    BOT_ID = app.client.auth_test()["user_id"]
    logger.info(f"Bot authenticated successfully. BOT_ID: {BOT_ID}")
except Exception as e:
    logger.error(f"Failed to fetch BOT_ID: {e}")
    BOT_ID = "UNKNOWN"

# --- CONFIGURABLE IN-MEMORY STATE ---
THREAD_CONTEXT_LIMIT = int(os.getenv("THREAD_CONTEXT_LIMIT", "10"))
THROTTLE_WINDOW_SEC = int(os.getenv("THROTTLE_WINDOW_SEC", "5"))
MAX_WARNINGS = int(os.getenv("MAX_WARNINGS", "3"))
WARNING_WINDOW_MIN = int(os.getenv("WARNING_WINDOW_MIN", "10"))
BAN_DURATION_MIN = int(os.getenv("BAN_DURATION_MIN", "60"))

_user_info_cache = {}                 # user_id -> {"user_id": ..., "display_name": ..., "real_name": ...}

# State Stores
user_last_msg_time = {}               # user_id -> float timestamp
user_warnings = defaultdict(list)     # user_id -> list of float timestamps
user_bans = {}                        # user_id -> ban_expiry_timestamp
silenced_threads = set()              # set of thread_ts strings
thread_locks = defaultdict(threading.Lock)  # thread_ts -> Lock (enforces sequential thread execution)

def get_user_info(user_id):
    if user_id in _user_info_cache:
        return _user_info_cache[user_id]
    try:
        result = app.client.users_info(user=user_id)
        user = result["user"]
        profile = user.get("profile", {})
        info = {
            "user_id": user_id,
            "display_name": profile.get("display_name") or user.get("name", user_id),
            "real_name": profile.get("real_name", ""),
        }
    except Exception as e:
        logger.warning(f"Failed to resolve user info for {user_id}: {e}")
        info = {"user_id": user_id, "display_name": user_id, "real_name": ""}
    _user_info_cache[user_id] = info
    return info


def get_thread_context(channel_id, thread_ts, current_ts):
    if thread_ts == current_ts:
        return {
            "bot_has_participated": False,
            "sender_is_sole_human": True,
            "participant_count": 1,
            "recent_messages": [],
        }
    try:
        result = app.client.conversations_replies(
            channel=channel_id, ts=thread_ts, limit=THREAD_CONTEXT_LIMIT
        )
        messages = result.get("messages", [])
    except Exception as e:
        logger.warning(f"Failed to fetch thread context: {e}")
        return {
            "bot_has_participated": False,
            "sender_is_sole_human": True,
            "participant_count": 1,
            "recent_messages": [],
        }

    bot_has_participated = any(
        m.get("bot_id") or m.get("user") == BOT_ID for m in messages
    )
    human_users = {
        m.get("user") for m in messages
        if m.get("user") and m.get("user") != BOT_ID and not m.get("bot_id")
    }
    recent_messages = []
    for m in messages:
        uid = m.get("user", "")
        user_info = get_user_info(uid) if uid else {"user_id": "", "display_name": "bot", "real_name": ""}
        recent_messages.append({
            "user": uid,
            "display_name": user_info["display_name"],
            "text": m.get("text", ""),
            "ts": m.get("ts", ""),
        })

    return {
        "bot_has_participated": bot_has_participated,
        "sender_is_sole_human": len(human_users) <= 1,
        "participant_count": len(human_users) + (1 if bot_has_participated else 0),
        "recent_messages": recent_messages,
    }


def get_cognito_token():
    client_id = os.getenv('COGNITO_CLIENT_ID')
    client_secret = os.getenv('COGNITO_CLIENT_SECRET')
    domain = os.getenv('COGNITO_DOMAIN')
    
    auth_b64 = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    logger.info("[AUTH] Fetching fresh Cognito M2M access token...")
    
    response = requests.post(
        f"{domain}/oauth2/token",
        headers={"Authorization": f"Basic {auth_b64}"},
        data={"grant_type": "client_credentials", "scope": "agent-api/invoke"}
    )
    return response.json()["access_token"]



def make_session_id(channel_id, thread_ts):
    raw = f"{channel_id}:{thread_ts}"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return f"slack_{digest}"

def invoke_agentcore(payload, channel_id, thread_ts, include_user_id=False):
    token = get_cognito_token()
    url = os.getenv("AGENT_URL")

    session_id = make_session_id(channel_id, thread_ts)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
    }

    if include_user_id:
        headers["X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Id"] = channel_id

    logger.info(
        f"[HTTP OUTBOUND] Calling AgentCore API. "
        f"SlackThread: {thread_ts} | RuntimeSessionId: {session_id} | "
        f"Action: {payload.get('action', 'standard_invoke')}"
    )

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        stream=True,
    )

    response.raise_for_status()

    full_response = ""
    for line in response.iter_lines():
        if line:
            decoded = line.decode("utf-8")
            if decoded.startswith("data: "):
                try:
                    full_response += json.loads(decoded[6:])
                except json.JSONDecodeError:
                    continue

    logger.info(
        f"[HTTP RESPONSE] Received {len(full_response)} bytes from AgentCore."
    )
    return full_response.strip()


def _http_url_to_ws(url):
    """Convert an AgentCore HTTP URL to its WebSocket equivalent.

    Replaces /invocations suffix with /ws, or appends /ws if no /invocations.
    """
    parsed = urlparse(url)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    path = parsed.path.rstrip("/")
    if path.endswith("/invocations"):
        path = path[:-len("/invocations")] + "/ws"
    else:
        path = path + "/ws"
    return f"{ws_scheme}://{parsed.netloc}{path}"


def invoke_agentcore_ws(payload, channel_id, thread_ts, include_user_id=False):
    """Invoke AgentCore via WebSocket. Returns the full response text."""
    import websocket as ws_client

    token = get_cognito_token()
    http_url = os.getenv("AGENT_URL")
    ws_url = _http_url_to_ws(http_url)

    session_id = make_session_id(channel_id, thread_ts)

    headers = {
        "Authorization": f"Bearer {token}",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
    }
    if include_user_id:
        headers["X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Id"] = channel_id

    logger.info(
        f"[WS OUTBOUND] Connecting to AgentCore WebSocket. "
        f"SlackThread: {thread_ts} | RuntimeSessionId: {session_id} | "
        f"Action: {payload.get('action', 'standard_invoke')}"
    )

    conn = ws_client.create_connection(
        ws_url,
        header=[f"{k}: {v}" for k, v in headers.items()],
        timeout=120,
    )

    try:
        if _client_tools:
            schemas = [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in _client_tools]
            payload["client_tools"] = schemas
            logger.info(f"[MCP INJECT] Injected {len(schemas)} client tool schemas into payload.")

        conn.send(json.dumps(payload))

        full_response = ""
        while True:
            try:
                raw = conn.recv()
            except ws_client.WebSocketConnectionClosedException:
                logger.error("[WS DISCONNECT] Connection closed unexpectedly during receive.")
                if full_response:
                    return full_response.strip()
                return "Sorry, the connection to the server was lost. Please try again."

            if not raw:
                break
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "text_delta":
                full_response += msg.get("content", "")
            elif msg_type == "done":
                break
            elif msg_type == "tool_call":
                tool_call_id = msg.get("tool_call_id")
                tool_name = msg.get("name")
                tool_args = msg.get("args", {})
                logger.info(f"[WS TOOL_CALL] Received tool_call: {tool_name} (id={tool_call_id})")
                content = _execute_mcp_tool(tool_name, tool_args)
                logger.info(f"[WS TOOL_RESULT] Returning result for {tool_name} (id={tool_call_id}): {content[:80]}...")
                try:
                    conn.send(json.dumps({
                        "type": "tool_result",
                        "tool_call_id": tool_call_id,
                        "content": content,
                    }))
                except ws_client.WebSocketConnectionClosedException:
                    logger.error("[WS DISCONNECT] Connection closed while sending tool_result.")
                    return "Sorry, the connection to the server was lost during tool execution. Please try again."
            elif msg_type == "error":
                logger.error(f"[WS ERROR] AgentCore returned error: {msg.get('content')}")
                return f"Sorry, something went wrong: {msg.get('content', 'unknown error')}"
    finally:
        try:
            conn.close()
        except Exception:
            pass

    logger.info(f"[WS RESPONSE] Received {len(full_response)} bytes from AgentCore.")
    result = full_response.strip()
    if not result:
        logger.warning("[WS RESPONSE] Empty response from AgentCore — possible agent error.")
        return "Sorry, I wasn't able to generate a response. Please try again."
    return result


USE_WEBSOCKET = os.getenv("USE_WEBSOCKET", "false").lower() in ("true", "1", "yes")
MCP_TOOL_TIMEOUT = int(os.getenv("MCP_TOOL_TIMEOUT", "30"))

# --- MCP TOOL CONFIG ---

_client_tools = []

def _load_mcp_tools():
    """Load local MCP tool schemas from mcp_tools.json. Returns list of tool dicts."""
    config_path = os.path.join(os.path.dirname(__file__) or ".", "mcp_tools.json")
    if not os.path.exists(config_path):
        logger.info("[MCP CONFIG] No mcp_tools.json found. Starting without client tools.")
        return []
    try:
        with open(config_path) as f:
            config = json.load(f)
        servers = config.get("servers", [])
        tools = []
        for server in servers:
            server_name = server.get("name", "unknown")
            for t in server.get("tools", []):
                t["_server"] = server_name
                t["_command"] = server.get("command")
                t["_transport"] = server.get("transport", "stdio")
                t["_url"] = server.get("url")
                tools.append(t)
        logger.info(f"[MCP CONFIG] Loaded {len(tools)} client tools from {len(servers)} servers.")
        return tools
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error(f"[MCP CONFIG] Failed to parse mcp_tools.json: {e}. Starting without client tools.")
        return []
    except Exception as e:
        logger.error(f"[MCP CONFIG] Unexpected error loading mcp_tools.json: {e}. Starting without client tools.")
        return []

_client_tools = _load_mcp_tools()

# Build lookup: tool_name -> server config for routing
_tool_to_server = {}
for _t in _client_tools:
    _tool_to_server[_t["name"]] = {
        "name": _t.get("_server"),
        "command": _t.get("_command"),
        "transport": _t.get("_transport", "stdio"),
        "url": _t.get("_url"),
    }


def _execute_mcp_tool(tool_name, args):
    """Execute a tool call against its configured local MCP server via stdio or HTTP."""
    server = _tool_to_server.get(tool_name)
    if not server:
        return f"Error: no MCP server configured for tool '{tool_name}'"

    transport = server.get("transport", "stdio")
    logger.info(f"[MCP EXEC] Calling tool '{tool_name}' via {server['name']} (transport={transport})")

    if transport == "http":
        return _execute_mcp_tool_http(tool_name, args, server)
    return _execute_mcp_tool_stdio(tool_name, args, server)


def _execute_mcp_tool_stdio(tool_name, args, server):
    """Execute a tool call via stdio subprocess JSON-RPC."""
    import subprocess

    if not server.get("command"):
        return f"Error: no command configured for stdio server '{server['name']}'"

    rpc_request = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": args},
    })

    try:
        result = subprocess.run(
            server["command"],
            input=rpc_request,
            capture_output=True,
            text=True,
            timeout=MCP_TOOL_TIMEOUT,
        )
        if result.returncode != 0:
            logger.warning(f"[MCP EXEC] Server {server['name']} exited with code {result.returncode}: {result.stderr[:200]}")
            return f"Error: MCP server exited with code {result.returncode}: {result.stderr[:200]}"

        return _parse_rpc_response(result.stdout)

    except subprocess.TimeoutExpired:
        logger.warning(f"[MCP EXEC] Tool '{tool_name}' timed out after {MCP_TOOL_TIMEOUT}s")
        return f"Error: tool execution timed out after {MCP_TOOL_TIMEOUT}s"
    except FileNotFoundError:
        logger.error(f"[MCP EXEC] Command not found: {server['command']}")
        return f"Error: MCP server command not found: {server['command'][0]}"
    except Exception as e:
        logger.error(f"[MCP EXEC] Unexpected error executing tool '{tool_name}': {e}")
        return f"Error: {e}"


def _execute_mcp_tool_http(tool_name, args, server):
    """Execute a tool call via MCP Streamable HTTP (POST JSON-RPC to server URL)."""
    url = server.get("url")
    if not url:
        return f"Error: no url configured for HTTP server '{server['name']}'"

    rpc_request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": args},
    }

    try:
        resp = requests.post(
            url,
            json=rpc_request,
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            timeout=MCP_TOOL_TIMEOUT,
        )
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "")
        if "text/event-stream" in content_type:
            return _parse_sse_response(resp.text)

        return _parse_rpc_response(resp.text)

    except requests.Timeout:
        logger.warning(f"[MCP EXEC] HTTP tool '{tool_name}' timed out after {MCP_TOOL_TIMEOUT}s")
        return f"Error: tool execution timed out after {MCP_TOOL_TIMEOUT}s"
    except requests.RequestException as e:
        logger.error(f"[MCP EXEC] HTTP error calling tool '{tool_name}': {e}")
        return f"Error: {e}"
    except Exception as e:
        logger.error(f"[MCP EXEC] Unexpected error executing tool '{tool_name}': {e}")
        return f"Error: {e}"


def _parse_rpc_response(raw):
    """Parse a JSON-RPC response and extract MCP content."""
    try:
        rpc_response = json.loads(raw)
        if "error" in rpc_response:
            return f"Error: {rpc_response['error'].get('message', 'unknown MCP error')}"
        rpc_result = rpc_response.get("result", {})
        content_parts = rpc_result.get("content", [])
        return "\n".join(p.get("text", str(p)) for p in content_parts) if content_parts else str(rpc_result)
    except json.JSONDecodeError:
        return raw.strip() or "Error: empty response from MCP server"


def _parse_sse_response(raw):
    """Parse an SSE stream for the JSON-RPC response event."""
    for line in raw.split("\n"):
        if line.startswith("data: "):
            data = line[6:].strip()
            if data:
                return _parse_rpc_response(data)
    return "Error: no data event in SSE response"


def invoke_agentcore_auto(payload, channel_id, thread_ts, include_user_id=False):
    """Route to WebSocket or HTTP based on USE_WEBSOCKET env var. Falls back to HTTP on WS failure."""
    if USE_WEBSOCKET:
        try:
            return invoke_agentcore_ws(payload, channel_id, thread_ts, include_user_id)
        except Exception as e:
            logger.warning(f"[WS FALLBACK] WebSocket invocation failed ({e}), falling back to HTTP.")
    return invoke_agentcore(payload, channel_id, thread_ts, include_user_id)


def download_slack_image(files):
    if not files:
        return None
    for f in files:
        mimetype = f.get("mimetype", "")
        if mimetype.startswith("image/"):
            url = f.get("url_private")
            logger.info(f"[IMAGE PROCESSING] Downloading attached image from Slack: {f.get('name')}")
            headers = {"Authorization": f"Bearer {os.getenv('SLACK_BOT_TOKEN')}"}
            res = requests.get(url, headers=headers)
            if res.status_code == 200:
                logger.info("[IMAGE PROCESSING] Successfully encoded image to base64.")
                return base64.b64encode(res.content).decode("utf-8")
    return None

def react(channel, ts, emoji):
    try:
        app.client.reactions_add(channel=channel, timestamp=ts, name=emoji)
    except Exception as e:
        logger.warning(f"Failed to add reaction :{emoji}: -> {e}")

def unreact(channel, ts, emoji):
    try:
        app.client.reactions_remove(channel=channel, timestamp=ts, name=emoji)
    except Exception as e:
        logger.warning(f"Failed to remove reaction :{emoji}: -> {e}")

# --- SLACK EVENT HANDLERS ---

@app.event("app_mention")
def handle_app_mention(body, logger):
    # Quietly acknowledge app_mention to prevent Bolt duplicate handler warnings.
    pass

@app.event("message")
def handle_message(body, logger, say):
    event = body.get("event", {})
    
    # 1. Ignore bot messages
    if "bot_id" in event or event.get("subtype") == "bot_message":
        return

    user_id = event.get("user")
    text = event.get("text", "")
    channel_id = event.get("channel")
    ts = event.get("ts")
    thread_ts = event.get("thread_ts") or event.get("ts")
    now = time.time()

    logger.info(f"[THREAD RESOLUTION] Message TS: {ts} | Resolved Root Thread ID: {thread_ts}")

    logger.info(f"[INGRESS MESSAGE] Channel: {channel_id} | User: {user_id} | Thread: {thread_ts} | Text: '{text}'")

    # --- BUILD CONVERSATION CONTEXT ---
    speaker = get_user_info(user_id)
    thread_context = get_thread_context(channel_id, thread_ts, ts)
    logger.info(f"[CONTEXT] Speaker: {speaker['display_name']} | Bot participated: {thread_context['bot_has_participated']} | Sole human: {thread_context['sender_is_sole_human']}")

    # --- 2. THREAD SILENCING COMMAND CHECK ---
    if f"<@{BOT_ID}> silence" in text.lower() or "/silence" in text.lower():
        silenced_threads.add(thread_ts)
        logger.info(f"[STATE ACTION] Thread {thread_ts} has been SILENCED.")
        say(text="🤐 This thread has been silenced. I will no longer respond here.", thread_ts=thread_ts)
        return

    if thread_ts in silenced_threads:
        logger.info(f"[STATE CHECK] Message ignored. Thread {thread_ts} is currently silenced.")
        return

    # --- 3. BANNED USER CHECK ---
    if user_id in user_bans:
        if now < user_bans[user_id]:
            logger.info(f"[STATE CHECK] Message dropped. User {user_id} is BANNED until {user_bans[user_id]}")
            return
        else:
            del user_bans[user_id]
            logger.info(f"[STATE ACTION] Ban expired for user {user_id}.")

    # --- 4. THROTTLING CHECK ---
    last_time = user_last_msg_time.get(user_id, 0)
    if now - last_time < THROTTLE_WINDOW_SEC:
        logger.warning(f"[THROTTLE DROP] User {user_id} exceeded rate limit ({THROTTLE_WINDOW_SEC}s window). Message dropped.")
        return
    user_last_msg_time[user_id] = now

    # --- 5. THREAD QUEUEING / LOCKING ---
    # Acquire thread lock so multiple messages in the same thread process sequentially
    with thread_locks[thread_ts]:
        logger.info(f"[THREAD LOCK ACQUIRED] Processing thread {thread_ts}")
        
        is_tagged = f"<@{BOT_ID}>" in text
        image_b64 = download_slack_image(event.get("files"))

        # --- 6. GATE EVALUATION PHASE ---
        if is_tagged:
            logger.info("[GATE] Bot directly TAGGED. Skipping ambient gate -> proceeding directly to answer.")
            react(channel_id, ts, "hourglass")
        else:
            logger.info("[GATE] AMBIENT message detected. Evaluating intent via AgentCore...")
            react(channel_id, ts, "thinking_face")
            
            gate_eval = invoke_agentcore_auto({
                "action": "evaluate_gate",
                "prompt": text,
                "thread_context": {
                    "speaker": speaker,
                    "thread": thread_context,
                },
            }, channel_id, thread_ts, include_user_id=True)
            logger.info(f"[GATE RESULT] AgentCore raw output: '{gate_eval}'")
            
            try:
                gate_json = json.loads(gate_eval)
                action = gate_json.get("action", "IGNORE")
            except Exception:
                action = "IGNORE"

            if action == "IGNORE":
                logger.info("[GATE DECISION] Result: IGNORE. Reacting with :thumbsup:")
                unreact(channel_id, ts, "thinking_face")
                react(channel_id, ts, "thumbsup")
                return

            elif action == "INAPPROPRIATE":
                logger.warning(f"[GATE DECISION] Result: INAPPROPRIATE content from user {user_id}.")
                unreact(channel_id, ts, "thinking_face")
                react(channel_id, ts, "point_up")
                
                # Record Warning Strike
                user_warnings[user_id].append(now)
                # Prune old warnings outside window
                user_warnings[user_id] = [t for t in user_warnings[user_id] if now - t <= (WARNING_WINDOW_MIN * 60)]
                
                strike_count = len(user_warnings[user_id])
                logger.warning(f"[WARNING STRIKE] User {user_id} now has {strike_count}/{MAX_WARNINGS} strikes.")

                if strike_count >= MAX_WARNINGS:
                    ban_until = now + (BAN_DURATION_MIN * 60)
                    user_bans[user_id] = ban_until
                    logger.error(f"[BAN TRIGGERED] User {user_id} BANNED for {BAN_DURATION_MIN} minutes.")
                    react(channel_id, ts, "see_no_evil")
                    react(channel_id, ts, "hear_no_evil")
                    react(channel_id, ts, "speak_no_evil")
                    say(text=f"🚫 You have been banned for {BAN_DURATION_MIN} minutes due to repeated policy violations.", thread_ts=thread_ts)
                else:
                    say(text=f"⚠️ Please keep channel interactions appropriate. (Warning {strike_count}/{MAX_WARNINGS})", thread_ts=thread_ts)
                return

            elif action == "RESPOND":
                logger.info("[GATE DECISION] Result: RESPOND. Swapping :thinking_face: -> :hourglass:")
                unreact(channel_id, ts, "thinking_face")
                react(channel_id, ts, "hourglass")

        # --- 7. ANSWER GENERATION PHASE ---
        logger.info(f"[MODEL CALL] Executing main AgentCore response stream for thread {thread_ts}...")
        
        payload = {
            "prompt": text,
            "speaker": speaker,
            "channel_id": channel_id,
            "thread_context": {
                "speaker": speaker,
                "thread": thread_context,
            },
        }
        if image_b64:
            payload["image_b64"] = image_b64

        agent_reply = invoke_agentcore_auto(payload, channel_id, thread_ts)
        
        # Post answer and update reactions
        say(text=agent_reply, thread_ts=thread_ts)
        unreact(channel_id, ts, "hourglass")
        react(channel_id, ts, "bulb")
        logger.info(f"[COMPLETED] Successfully responded to thread {thread_ts}")

if __name__ == "__main__":
    logger.info("⚡️ Starting Slack Harness in Socket Mode...")
    handler = SocketModeHandler(app, os.getenv("SLACK_APP_TOKEN"))
    handler.start()