"""bot.py — Slack harness for Herocore.

All MCP tool loading, WebSocket connection management, and AgentCore invocation
logic now lives in the ``herocore_bridge`` library.  This file is responsible
only for Slack event handling, rate-limiting, and routing to AgentCore.

Install the library before running:
    pip install -e packages/herocore-bridge
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
import time
from collections import defaultdict

import requests
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from herocore_bridge import load_mcp_tools, invoke_agentcore_ws
from herocore_bridge.harness_client import invoke_agentcore_http

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("SlackHarness")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
load_dotenv()

app = App(token=os.getenv("SLACK_BOT_TOKEN"))

try:
    BOT_ID = app.client.auth_test()["user_id"]
    logger.info("Bot authenticated successfully. BOT_ID: %s", BOT_ID)
except Exception as e:
    logger.error("Failed to fetch BOT_ID: %s", e)
    BOT_ID = "UNKNOWN"

THREAD_CONTEXT_LIMIT = int(os.getenv("THREAD_CONTEXT_LIMIT", "10"))
THROTTLE_WINDOW_SEC = int(os.getenv("THROTTLE_WINDOW_SEC", "5"))
MAX_WARNINGS = int(os.getenv("MAX_WARNINGS", "3"))
WARNING_WINDOW_MIN = int(os.getenv("WARNING_WINDOW_MIN", "10"))
BAN_DURATION_MIN = int(os.getenv("BAN_DURATION_MIN", "60"))

USE_WEBSOCKET = os.getenv("USE_WEBSOCKET", "false").lower() in ("true", "1", "yes")

# ---------------------------------------------------------------------------
# MCP tools — loaded once at startup via herocore_bridge
# ---------------------------------------------------------------------------
_client_tools = load_mcp_tools()

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------
_user_info_cache: dict = {}
user_last_msg_time: dict = {}
user_warnings: dict = defaultdict(list)
user_bans: dict = {}
silenced_threads: set = set()
thread_locks: dict = defaultdict(threading.Lock)


# ---------------------------------------------------------------------------
# Cognito auth helper (harness-specific, stays here)
# ---------------------------------------------------------------------------
def get_cognito_token() -> str:
    client_id = os.getenv("COGNITO_CLIENT_ID")
    client_secret = os.getenv("COGNITO_CLIENT_SECRET")
    domain = os.getenv("COGNITO_DOMAIN")
    auth_b64 = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    logger.info("[AUTH] Fetching fresh Cognito M2M access token...")
    response = requests.post(
        f"{domain}/oauth2/token",
        headers={"Authorization": f"Basic {auth_b64}"},
        data={"grant_type": "client_credentials", "scope": "agent-api/invoke"},
    )
    return response.json()["access_token"]


def make_session_id(channel_id: str, thread_ts: str) -> str:
    digest = hashlib.sha256(f"{channel_id}:{thread_ts}".encode()).hexdigest()
    return f"slack_{digest}"


# ---------------------------------------------------------------------------
# AgentCore invocation (delegates to herocore_bridge)
# ---------------------------------------------------------------------------
def invoke_agentcore_auto(
    payload: dict,
    channel_id: str,
    thread_ts: str,
    include_user_id: bool = False,
) -> str:
    """Route to WebSocket or HTTP AgentCore invocation."""
    session_id = make_session_id(channel_id, thread_ts)
    shared_kwargs = dict(
        channel_id=channel_id,
        thread_ts=thread_ts,
        cognito_token_fn=get_cognito_token,
        agent_url=os.getenv("AGENT_URL"),
        session_id=session_id,
        include_user_id=include_user_id,
    )

    if USE_WEBSOCKET:
        try:
            return invoke_agentcore_ws(
                payload,
                client_tools=_client_tools,
                **shared_kwargs,
            )
        except Exception as e:
            logger.warning("[WS FALLBACK] WebSocket invocation failed (%s), falling back to HTTP.", e)

    return invoke_agentcore_http(payload, **shared_kwargs)


# ---------------------------------------------------------------------------
# Slack helpers
# ---------------------------------------------------------------------------
def get_user_info(user_id: str) -> dict:
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
        logger.warning("Failed to resolve user info for %s: %s", user_id, e)
        info = {"user_id": user_id, "display_name": user_id, "real_name": ""}
    _user_info_cache[user_id] = info
    return info


def get_thread_context(channel_id: str, thread_ts: str, current_ts: str) -> dict:
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
        logger.warning("Failed to fetch thread context: %s", e)
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
        m.get("user")
        for m in messages
        if m.get("user") and m.get("user") != BOT_ID and not m.get("bot_id")
    }
    recent_messages = []
    for m in messages:
        uid = m.get("user", "")
        user_info = (
            get_user_info(uid) if uid else {"user_id": "", "display_name": "bot", "real_name": ""}
        )
        recent_messages.append(
            {
                "user": uid,
                "display_name": user_info["display_name"],
                "text": m.get("text", ""),
                "ts": m.get("ts", ""),
            }
        )

    return {
        "bot_has_participated": bot_has_participated,
        "sender_is_sole_human": len(human_users) <= 1,
        "participant_count": len(human_users) + (1 if bot_has_participated else 0),
        "recent_messages": recent_messages,
    }


def download_slack_image(files: list | None) -> str | None:
    if not files:
        return None
    for f in files:
        if f.get("mimetype", "").startswith("image/"):
            url = f.get("url_private")
            logger.info("[IMAGE PROCESSING] Downloading attached image: %s", f.get("name"))
            res = requests.get(
                url, headers={"Authorization": f"Bearer {os.getenv('SLACK_BOT_TOKEN')}"}
            )
            if res.status_code == 200:
                logger.info("[IMAGE PROCESSING] Successfully encoded image to base64.")
                return base64.b64encode(res.content).decode("utf-8")
    return None


def react(channel: str, ts: str, emoji: str) -> None:
    try:
        app.client.reactions_add(channel=channel, timestamp=ts, name=emoji)
    except Exception as e:
        logger.warning("Failed to add reaction :%s: -> %s", emoji, e)


def unreact(channel: str, ts: str, emoji: str) -> None:
    try:
        app.client.reactions_remove(channel=channel, timestamp=ts, name=emoji)
    except Exception as e:
        logger.warning("Failed to remove reaction :%s: -> %s", emoji, e)


# ---------------------------------------------------------------------------
# Slack event handlers
# ---------------------------------------------------------------------------
@app.event("app_mention")
def handle_app_mention(body, logger):  # noqa: ARG001
    # Suppress Bolt duplicate-handler warnings; logic lives in handle_message.
    pass


@app.event("message")
def handle_message(body, logger, say):  # noqa: ARG001
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

    logger.info(
        "[INGRESS MESSAGE] Channel: %s | User: %s | Thread: %s | Text: '%s'",
        channel_id, user_id, thread_ts, text,
    )

    speaker = get_user_info(user_id)
    thread_context = get_thread_context(channel_id, thread_ts, ts)
    logger.info(
        "[CONTEXT] Speaker: %s | Bot participated: %s | Sole human: %s",
        speaker["display_name"],
        thread_context["bot_has_participated"],
        thread_context["sender_is_sole_human"],
    )

    # --- 2. Thread silencing ---
    if f"<@{BOT_ID}> silence" in text.lower() or "/silence" in text.lower():
        silenced_threads.add(thread_ts)
        say(text="🤐 This thread has been silenced. I will no longer respond here.", thread_ts=thread_ts)
        return

    if thread_ts in silenced_threads:
        return

    # --- 3. Ban check ---
    if user_id in user_bans:
        if now < user_bans[user_id]:
            return
        del user_bans[user_id]

    # --- 4. Throttle ---
    last_time = user_last_msg_time.get(user_id, 0)
    if now - last_time < THROTTLE_WINDOW_SEC:
        logger.warning("[THROTTLE DROP] User %s exceeded rate limit. Message dropped.", user_id)
        return
    user_last_msg_time[user_id] = now

    # --- 5. Thread lock (sequential processing per thread) ---
    with thread_locks[thread_ts]:
        is_tagged = f"<@{BOT_ID}>" in text
        image_b64 = download_slack_image(event.get("files"))

        # --- 6. Gate evaluation ---
        if is_tagged:
            logger.info("[GATE] Bot directly TAGGED. Proceeding to answer.")
            react(channel_id, ts, "hourglass")
        else:
            logger.info("[GATE] AMBIENT message. Evaluating intent via AgentCore...")
            react(channel_id, ts, "thinking_face")

            gate_eval = invoke_agentcore_auto(
                {
                    "action": "evaluate_gate",
                    "prompt": text,
                    "thread_context": {"speaker": speaker, "thread": thread_context},
                },
                channel_id,
                thread_ts,
                include_user_id=True,
            )
            logger.info("[GATE RESULT] AgentCore raw output: '%s'", gate_eval)

            try:
                action = json.loads(gate_eval).get("action", "IGNORE")
            except Exception:
                action = "IGNORE"

            if action == "IGNORE":
                unreact(channel_id, ts, "thinking_face")
                react(channel_id, ts, "thumbsup")
                return

            elif action == "INAPPROPRIATE":
                unreact(channel_id, ts, "thinking_face")
                react(channel_id, ts, "point_up")

                user_warnings[user_id].append(now)
                user_warnings[user_id] = [
                    t for t in user_warnings[user_id] if now - t <= (WARNING_WINDOW_MIN * 60)
                ]
                strike_count = len(user_warnings[user_id])

                if strike_count >= MAX_WARNINGS:
                    ban_until = now + (BAN_DURATION_MIN * 60)
                    user_bans[user_id] = ban_until
                    react(channel_id, ts, "see_no_evil")
                    react(channel_id, ts, "hear_no_evil")
                    react(channel_id, ts, "speak_no_evil")
                    say(
                        text=f"🚫 You have been banned for {BAN_DURATION_MIN} minutes due to repeated policy violations.",
                        thread_ts=thread_ts,
                    )
                else:
                    say(
                        text=f"⚠️ Please keep channel interactions appropriate. (Warning {strike_count}/{MAX_WARNINGS})",
                        thread_ts=thread_ts,
                    )
                return

            elif action == "RESPOND":
                unreact(channel_id, ts, "thinking_face")
                react(channel_id, ts, "hourglass")

        # --- 7. Answer generation ---
        payload = {
            "prompt": text,
            "speaker": speaker,
            "channel_id": channel_id,
            "thread_context": {"speaker": speaker, "thread": thread_context},
        }
        if image_b64:
            payload["image_b64"] = image_b64

        agent_reply = invoke_agentcore_auto(payload, channel_id, thread_ts)

        say(text=agent_reply, thread_ts=thread_ts)
        unreact(channel_id, ts, "hourglass")
        react(channel_id, ts, "bulb")
        logger.info("[COMPLETED] Successfully responded to thread %s", thread_ts)


if __name__ == "__main__":
    logger.info("⚡️ Starting Slack Harness in Socket Mode...")
    handler = SocketModeHandler(app, os.getenv("SLACK_APP_TOKEN"))
    handler.start()
