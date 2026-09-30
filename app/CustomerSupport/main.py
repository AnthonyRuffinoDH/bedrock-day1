from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from model.load import load_model
from mcp_client.client import get_streamable_http_mcp_client, get_gateway_mcp_client
from memory.session import get_channel_memory_session_manager
from herocore_bridge.agent_proxy import build_client_tool_wrappers, CLIENT_NS
import asyncio
import functools
import logging
import json
import os
import pathlib
from typing import Any
from datetime import datetime, timezone

import httpx

app = BedrockAgentCoreApp()
log = app.logger
log.setLevel(logging.INFO)

# MCP clients: Exa AI (web search) + AgentCore Gateway (Lambda tools)
mcp_clients = [get_streamable_http_mcp_client(), get_gateway_mcp_client()]

# --- Tool Namespacing ---
# Agent-side and client-side tools are registered in separate namespaces so
# both sides can define tools with identical base names without collision.
AGENT_NS = "agent"


def _ns_tool(namespace, func):
    """Wrap a plain function as a Strands @tool with a namespaced name."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    wrapper.__name__ = f"{namespace}__{func.__name__}"
    return tool(wrapper)


SYSTEM_PROMPT = """You are Herocore, an autonomous, self-improving AI system deployed via a Slack WebSocket harness.
Your primary directive is to understand your own architecture, explore your codebase, and propose structural improvements to your capabilities.

<conversational_context>
Your primary source of conversational context is the recent thread messages included with each request. Use these to understand the ongoing conversation. You do not automatically have access to persistent memory.
</conversational_context>

<default_tools>
You have agent-side tools available by default. All tool invocations go through AgentCore — never make raw LLM calls.

Filesystem tools (read-only, scoped to your allowed directory):
- agent__read_file: Read file contents
- agent__list_directory: List directory entries with [FILE]/[DIR] prefixes
- agent__search_files: Recursively search for files matching a glob pattern
- agent__get_file_info: Get file metadata (size, modification time, type)

HTTP tools (for fetching web content):
- agent__http_get: Fetch a URL and extract relevant content via a nested extraction agent
- agent__http_head: Get response status and headers without downloading the body

Memory tools (persistent channel knowledge):
- agent__channel_memory_store: Save durably valuable knowledge for future conversations in this channel
- agent__channel_memory_recall: Query previously stored channel knowledge
</default_tools>

<client_tools>
You should also expect additional tools provided by the client/harness via MCP. These client-side tools (prefixed with client__) enable capabilities like code exploration, file modification, testing, and other development operations. When client tools are present, use them to carry out implementation work. When absent, operate with your default agent-side tools.

Client-side tools fall into two categories:
- Local filesystem tools (read_file, list_directory, etc.): Read-only access to the harness's local workspace. Fast, but reflects the local checkout — not necessarily the authoritative repository state.
- GitHub tools (get_file_contents, create_branch, create_or_update_file, create_pull_request, etc.): Read and write access to the authoritative GitHub repository. Use these for the self-improvement workflow.
</client_tools>

<github_workflow>
When asked to make a code change, follow this workflow:
1. EXPLORE: Read relevant files using agent-side filesystem tools or GitHub get_file_contents to understand current state.
2. BRANCH: Create a dedicated branch via client__create_branch (e.g., herocore/description-of-change).
3. CHANGE: Make the required changes via client__create_or_update_file. For updates to existing files, first get the file's SHA via client__get_file_contents.
4. PR: Open a pull request via client__create_pull_request explaining what changed and why.
5. STOP: Return the PR URL and stop. Do NOT merge the PR or trigger any deployment.

You MUST NOT merge pull requests. You MUST NOT trigger deployments or restarts. A human reviews and merges your work.
</github_workflow>

<operating_procedure>
When a user asks you to improve or modify a feature, follow this loop:
1. EXPLORE: Use your filesystem and client tools to progressively disclose and read the relevant source code files. Never guess how your code works — always read it first.
2. ANALYZE: Identify exactly what needs to be changed in both the harness and the core to achieve the goal.
3. PROPOSE: Output a formal proposal detailing the architecture changes, the specific files to modify, and the sequence of execution.
</operating_procedure>

<memory_guidelines>
Guidelines for memory tools:
- Do NOT store routine interactions. Only store information likely to remain useful beyond the current thread.
- When storing updates or resolutions to previously stored facts, always include the original topic and prior status in the content so future searches find the update.
- When you recall memory, treat it as supporting context, not authoritative. The active thread is always the primary source of truth.
- If recalled memory conflicts with what is visible in the current thread, trust the thread.
- Memory is optional. If a memory operation fails, continue the conversation normally.
</memory_guidelines>

<memory_reconciliation>
IMPORTANT — you MUST follow these rules when using recalled memory:
- If multiple entries about the same topic have contradictory statuses, treat the entry indicating resolution/completion/update as authoritative. Do NOT present an issue as ongoing when a resolution exists.
- You MUST NOT present recalled information as definitively current. Always qualify with temporal language: "As of the last update I have...", "The most recent record shows...", or "Based on what was previously stored...". Memory entries may be outdated.
- Never state "there IS an active issue" based solely on a recalled entry. Instead say "the last update I have indicates..." or similar.
- If only one entry is returned about an issue, explicitly note that you cannot confirm whether it is still current.
</memory_reconciliation>

<current_capabilities>
You have GitHub MCP integration: you can inspect repositories, create branches, commit file changes, and open pull requests. Your self-improvement boundary ends at opening a PR — merging and deployment are human-controlled. Your next evolutionary goals are CI/CD awareness and post-merge validation.
</current_capabilities>"""

# --- Filesystem Tools ---

_FS_ALLOWED_DIR = os.path.realpath(os.environ.get("FS_ALLOWED_DIR", os.getcwd()))


def _resolve_safe_path(requested_path: str) -> tuple[str, str | None]:
    """Resolve a path and validate it is within the allowed directory."""
    resolved = os.path.realpath(requested_path)
    try:
        common = os.path.commonpath([resolved, _FS_ALLOWED_DIR])
    except ValueError:
        return resolved, f"Path is not permitted: {requested_path}"
    if common != _FS_ALLOWED_DIR:
        return resolved, f"Path is not permitted: {requested_path}"
    return resolved, None


def read_file(path: str) -> str:
    """Read and return the contents of a file. The path must be within the allowed directory."""
    log.info(f"[TOOL EXECUTION] read_file called with path: '{path}'")
    resolved, err = _resolve_safe_path(path)
    if err:
        return err
    if not os.path.isfile(resolved):
        return f"File not found: {path}"
    with open(resolved, "r") as f:
        return f.read()


def list_directory(path: str) -> str:
    """List the contents of a directory, showing [FILE] or [DIR] prefix for each entry."""
    log.info(f"[TOOL EXECUTION] list_directory called with path: '{path}'")
    resolved, err = _resolve_safe_path(path)
    if err:
        return err
    if not os.path.isdir(resolved):
        return f"Directory not found: {path}"
    entries = []
    for entry in sorted(os.listdir(resolved)):
        full = os.path.join(resolved, entry)
        prefix = "[DIR]" if os.path.isdir(full) else "[FILE]"
        entries.append(f"{prefix} {entry}")
    return "\n".join(entries) if entries else "Directory is empty."


def search_files(path: str, pattern: str) -> str:
    """Recursively search for files matching a glob pattern within a directory."""
    log.info(f"[TOOL EXECUTION] search_files called with path: '{path}', pattern: '{pattern}'")
    resolved, err = _resolve_safe_path(path)
    if err:
        return err
    if not os.path.isdir(resolved):
        return f"Directory not found: {path}"
    matches = [str(p) for p in pathlib.Path(resolved).rglob(pattern)
               if _resolve_safe_path(str(p))[1] is None]
    if matches:
        return "\n".join(sorted(matches))
    return f"No files found matching '{pattern}' in {path}."


def get_file_info(path: str) -> str:
    """Get metadata about a file or directory: size, modification time, and type."""
    log.info(f"[TOOL EXECUTION] get_file_info called with path: '{path}'")
    resolved, err = _resolve_safe_path(path)
    if err:
        return err
    if not os.path.exists(resolved):
        return f"Path not found: {path}"
    stat = os.stat(resolved)
    file_type = "directory" if os.path.isdir(resolved) else "file"
    mod_time = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return f"Type: {file_type}, Size: {stat.st_size} bytes, Modified: {mod_time}"

# --- HTTP Tools ---

HTTP_TOOL_TIMEOUT = int(os.environ.get("HTTP_TOOL_TIMEOUT", "15"))
HTTP_MAX_RESPONSE_SIZE = int(os.environ.get("HTTP_MAX_RESPONSE_SIZE", "102400"))

WEB_EXTRACTION_PROMPT = """You are a web content extraction agent. You will receive raw web content \
fetched from a URL, along with recent thread context from the conversation that triggered this fetch.

Your job is to extract and return ONLY the information from the raw content that is relevant to the \
current conversation thread. Discard navigation, ads, boilerplate, and any content not related to \
what the user is asking about.

Return the relevant information concisely and factually. If the raw content contains no information \
relevant to the thread context, say so briefly."""

def http_head(url: str, headers: dict | None = None) -> str:
    """Perform an HTTP HEAD request and return the response status code and headers."""
    log.info(f"[TOOL EXECUTION] http_head called with url: '{url}'")
    try:
        with httpx.Client(timeout=HTTP_TOOL_TIMEOUT) as client:
            resp = client.head(url, headers=headers or {})
        header_lines = "\n".join(f"  {k}: {v}" for k, v in resp.headers.items())
        return f"Status: {resp.status_code}\nHeaders:\n{header_lines}"
    except Exception as e:
        return f"HTTP HEAD failed for {url}: {e}"


def http_get(url: str, headers: dict | None = None) -> str:
    """Fetch a URL via HTTP GET and return content extracted by a nested agent, filtered for relevance to the current conversation."""
    log.info(f"[TOOL EXECUTION] http_get called with url: '{url}'")
    try:
        with httpx.Client(timeout=HTTP_TOOL_TIMEOUT) as client:
            resp = client.get(url, headers=headers or {})
        body = resp.text
        truncated = False
        if len(body) > HTTP_MAX_RESPONSE_SIZE:
            body = body[:HTTP_MAX_RESPONSE_SIZE]
            truncated = True
    except Exception as e:
        return f"HTTP GET failed for {url}: {e}"

    precontext = ""
    try:
        ctx = _get_current_request_context()
        precontext = ctx.get("thread_precontext", "")
    except RuntimeError:
        pass

    extraction_input = f"URL: {url}\n"
    if truncated:
        extraction_input += "[Note: Response was truncated to the configured size limit.]\n"
    extraction_input += f"\n--- Raw Content ---\n{body}\n--- End Raw Content ---\n"
    if precontext:
        extraction_input += f"\n--- Thread Context ---\n{precontext}\n--- End Thread Context ---\n"

    try:
        extraction_agent = Agent(
            model=load_model(),
            system_prompt=WEB_EXTRACTION_PROMPT,
            tools=[],
        )
        result = extraction_agent(extraction_input)
        return str(result)
    except Exception as e:
        log.warning(f"[HTTP GET] Extraction agent failed: {e}")
        if truncated:
            return f"[Truncated to {HTTP_MAX_RESPONSE_SIZE} bytes]\n{body}"
        return body


MEMORY_AGENT_PROMPT = """You are a memory storage and retrieval agent. Process the request and respond concisely.

When storing information that updates, resolves, or changes a previously known fact, always reference \
the original topic in your stored content so that future searches for the original topic will find \
the update. For example, store "RESOLVED: The stale product catalog issue is now fixed" rather than \
just "catalog issue fixed"."""

PRE_RECALL_PROMPT = """You are a memory relevance filter. You will receive a user's message and \
have access to stored channel history via your memory context.

Your job is NOT to answer the user's question. Instead, determine what in the channel's stored \
history might be relevant to an agent who will answer this question.

If relevant history exists, return ONLY the relevant facts — concise, factual, no commentary.
If nothing in the channel history is relevant, return exactly: NO_RELEVANT_HISTORY"""

NO_RELEVANT_HISTORY = "NO_RELEVANT_HISTORY"


def channel_memory_store(content: str) -> str:
    """Store durable information scoped to the current Slack channel for future conversations. When storing updates or resolutions, include the original topic and prior status so the update supersedes older entries in future searches."""
    log.info(f"[MEMORY STORE] Storing channel memory: '{content[:60]}...'")
    try:
        ctx = _get_current_request_context()
        sm = get_channel_memory_session_manager(ctx["channel_id"], ctx["session_id"])
        if not sm:
            return "Memory service is not configured."
        memory_agent = Agent(
            model=load_model(),
            session_manager=sm,
            tools=TOOL_PROFILES["memory"](),
            system_prompt=MEMORY_AGENT_PROMPT,
        )
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        stamped_content = f"[{timestamp}] {content}"
        result = memory_agent(f"Remember this for future conversations in this channel: {stamped_content}")
        return f"Stored in channel memory: {stamped_content}"
    except Exception as e:
        log.warning(f"[MEMORY STORE] Failed: {e}")
        return "Failed to store in channel memory. Continuing without persistence."


def channel_memory_recall(query: str) -> str:
    """Query previously stored channel memory when prior knowledge may help answer a request."""
    log.info(f"[MEMORY RECALL] Querying channel memory: '{query[:60]}...'")
    try:
        ctx = _get_current_request_context()
        sm = get_channel_memory_session_manager(ctx["channel_id"], ctx["session_id"])
        if not sm:
            return "No channel memory available."
        memory_agent = Agent(
            model=load_model(),
            session_manager=sm,
            tools=TOOL_PROFILES["memory"](),
            system_prompt=MEMORY_AGENT_PROMPT,
        )
        result = memory_agent(f"What do you know about: {query}")
        return str(result)
    except Exception as e:
        log.warning(f"[MEMORY RECALL] Failed: {e}")
        return "No relevant channel memory found."


def pre_recall_channel_memory(prompt, channel_id, session_id):
    """Pre-recall channel memory for the first message in a thread. Returns relevant facts or None."""
    log.info(f"[PRE-RECALL] Querying channel memory for new session: '{prompt[:60]}...'")
    try:
        sm = get_channel_memory_session_manager(channel_id, session_id)
        if not sm:
            log.info("[PRE-RECALL] Memory service not configured, skipping.")
            return None
        recall_agent = Agent(
            model=load_model(),
            session_manager=sm,
            tools=TOOL_PROFILES["memory"](),
            system_prompt=PRE_RECALL_PROMPT,
        )
        result = str(recall_agent(prompt))
        if NO_RELEVANT_HISTORY in result:
            log.info("[PRE-RECALL] No relevant channel history found.")
            return None
        log.info(f"[PRE-RECALL] Relevant history found: '{result[:80]}...'")
        return result
    except Exception as e:
        log.warning(f"[PRE-RECALL] Failed: {e}")
        return None


_active_session_id = None


def _get_current_request_context():
    if _active_session_id and _active_session_id in _request_context:
        return _request_context[_active_session_id]
    raise RuntimeError("No active request context")


_filesystem_tools = [_ns_tool(AGENT_NS, f) for f in [read_file, list_directory, search_files, get_file_info]]
_http_tools = [_ns_tool(AGENT_NS, f) for f in [http_get, http_head]]
_mcp_tools = [c for c in mcp_clients if c]
_memory_tools = [_ns_tool(AGENT_NS, f) for f in [channel_memory_store, channel_memory_recall]]

TOOL_PROFILES = {
    "primary": lambda: _filesystem_tools + _http_tools + _mcp_tools + _memory_tools,
    "memory": lambda: [],
}

# Dict for caching active thread agents in local container RAM
_agents = {}

# Request-scoped context for memory tools to read channel_id/session_id
_request_context = {}

def get_or_create_agent(session_id, channel_id):
    global _agents

    if session_id not in _agents:
        log.info(
            f"[NEW INSTANCE] Hydrating session={session_id}, channel={channel_id}"
        )

        agent = Agent(
            model=load_model(),
            system_prompt=SYSTEM_PROMPT,
            tools=TOOL_PROFILES["primary"](),
        )

        _agents[session_id] = agent
    else:
        log.info(
            f"[WARM INSTANCE] Reusing agent for session={session_id}"
        )

    return _agents[session_id]

@app.entrypoint
async def invoke(payload, context):
    log.info(f"[AGENTCORE INVOKE] Received payload keys: {list(payload.keys())}")
    log.info(
        "[SESSION DEBUG] "
        f"context.session_id={context.session_id!r} | "
        f"payload.sessionId={payload.get('sessionId')!r}"
    )

    session_id = context.session_id

    if not session_id:
        raise ValueError("session_id is required.")

    action = payload.get("action")

    # --- 1. EVALUATION GATE ---
    if action == "evaluate_gate":
        log.info("[GATE EVALUATION] Running ambient message classification...")
        prompt_text = payload.get("prompt", "")
        tc = payload.get("thread_context", {})
        speaker_info = tc.get("speaker", {})
        thread_info = tc.get("thread", {})
        bot_participated = thread_info.get("bot_has_participated", False)
        sole_human = thread_info.get("sender_is_sole_human", True)
        recent = thread_info.get("recent_messages", [])

        thread_summary = ""
        if recent:
            thread_summary = "\nRecent thread messages:\n" + "\n".join(
                f"  {m.get('display_name', 'unknown')}: {m.get('text', '')}" for m in recent[-5:]
            )

        eval_prompt = (
            f"You are a help-biased channel routing classifier for a customer support bot.\n\n"
            f"Message from {speaker_info.get('display_name', 'unknown')}: \"{prompt_text}\"\n\n"
            f"Thread signals:\n"
            f"- Bot has already participated in this thread: {bot_participated}\n"
            f"- Sender is the only human in this thread: {sole_human}\n"
            f"{thread_summary}\n\n"
            f"Classification rules (apply in order):\n"
            f"1. If the message requests gambling, sports betting, financial, investment, or stock advice, or otherwise violates policies → {{\"action\": \"INAPPROPRIATE\"}}\n"
            f"2. If the bot has already participated in this thread, RESPOND unless the message is clearly a social sign-off (e.g., \"thanks\", \"bye\") → {{\"action\": \"RESPOND\"}}\n"
            f"3. If the message plausibly asks for help, asks a question, or could be a customer support request → {{\"action\": \"RESPOND\"}}\n"
            f"4. If the message is clearly human-to-human chatter, a social acknowledgement, or unrelated to support → {{\"action\": \"IGNORE\"}}\n"
            f"5. If ambiguous, default to → {{\"action\": \"RESPOND\"}}\n\n"
            f"Output ONLY valid raw JSON."
        )
        evaluator = Agent(
            model=load_model(),
            system_prompt="Output strict JSON only. Do not format as markdown."
        )
        stream = evaluator.stream_async(eval_prompt)
        async for event in stream:
            if "data" in event and isinstance(event["data"], str):
                yield event["data"]
        return

    # --- 2. STANDARD INVOCATION ---
    prompt_input = payload.get("prompt", "")
    image_b64 = payload.get("image_b64")
    speaker_meta = payload.get("speaker", {})
    channel_id = payload.get("channel_id", "")
    thread_ctx = payload.get("thread_context", {})

    context_block = ""
    if speaker_meta:
        context_block += f"[Speaker: {speaker_meta.get('display_name', 'unknown')}]\n"
    if channel_id:
        context_block += f"[Channel: {channel_id}]\n"
    thread_info = thread_ctx.get("thread", {})
    recent = thread_info.get("recent_messages", [])
    if recent:
        context_block += "[Recent thread messages]\n"
        for m in recent:
            context_block += f"  {m.get('display_name', 'unknown')}: {m.get('text', '')}\n"
    if context_block:
        prompt_input = f"{context_block}\n{prompt_input}"

    global _active_session_id
    _request_context[session_id] = {"channel_id": channel_id, "session_id": session_id, "thread_precontext": context_block}
    _active_session_id = session_id
    log.info(f"[REQUEST CONTEXT] Set _request_context for session={session_id}: channel={channel_id}")

    is_new_session = session_id not in _agents
    if is_new_session and channel_id:
        pre_recall_result = pre_recall_channel_memory(prompt_input, channel_id, session_id)
        if pre_recall_result:
            prompt_input = f"[Channel history]\n{pre_recall_result}\n\n{prompt_input}"
            log.info("[PRE-RECALL] Injected channel history into prompt context.")

    if image_b64:
        log.info("[MODEL CALL] Processing multi-modal input (Text + Image)...")
        prompt_data = [
            {"type": "text", "text": prompt_input or "Please describe and analyze this image."},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}}
        ]
    else:
        log.info(f"[MODEL CALL] Invoking main LLM with prompt: '{prompt_input[:60]}...'")
        prompt_data = prompt_input

    agent = get_or_create_agent(session_id, channel_id)
    stream = agent.stream_async(prompt_data)
    async for event in stream:
        if "data" in event and isinstance(event["data"], str):
            yield event["data"]

@app.websocket
async def ws_invoke(websocket, context):
    await websocket.accept()

    try:
        raw = await websocket.receive_text()
        payload = json.loads(raw)
    except Exception as e:
        await websocket.send_json({"type": "error", "content": str(e)})
        await websocket.close()
        return

    log.info(f"[WS INVOKE] Received payload keys: {list(payload.keys())}")

    session_id = context.session_id
    if not session_id:
        await websocket.send_json({"type": "error", "content": "session_id is required"})
        await websocket.close()
        return

    action = payload.get("action")

    # --- Gate evaluation over WebSocket ---
    if action == "evaluate_gate":
        log.info("[WS GATE] Running ambient message classification...")
        prompt_text = payload.get("prompt", "")
        tc = payload.get("thread_context", {})
        speaker_info = tc.get("speaker", {})
        thread_info = tc.get("thread", {})
        bot_participated = thread_info.get("bot_has_participated", False)
        sole_human = thread_info.get("sender_is_sole_human", True)
        recent = thread_info.get("recent_messages", [])

        thread_summary = ""
        if recent:
            thread_summary = "\nRecent thread messages:\n" + "\n".join(
                f"  {m.get('display_name', 'unknown')}: {m.get('text', '')}" for m in recent[-5:]
            )

        eval_prompt = (
            f"You are a help-biased channel routing classifier for a customer support bot.\n\n"
            f"Message from {speaker_info.get('display_name', 'unknown')}: \"{prompt_text}\"\n\n"
            f"Thread signals:\n"
            f"- Bot has already participated in this thread: {bot_participated}\n"
            f"- Sender is the only human in this thread: {sole_human}\n"
            f"{thread_summary}\n\n"
            f"Classification rules (apply in order):\n"
            f"1. If the message requests gambling, sports betting, financial, investment, or stock advice, or otherwise violates policies → {{\"action\": \"INAPPROPRIATE\"}}\n"
            f"2. If the bot has already participated in this thread, RESPOND unless the message is clearly a social sign-off (e.g., \"thanks\", \"bye\") → {{\"action\": \"RESPOND\"}}\n"
            f"3. If the message plausibly asks for help, asks a question, or could be a customer support request → {{\"action\": \"RESPOND\"}}\n"
            f"4. If the message is clearly human-to-human chatter, a social acknowledgement, or unrelated to support → {{\"action\": \"IGNORE\"}}\n"
            f"5. If ambiguous, default to → {{\"action\": \"RESPOND\"}}\n\n"
            f"Output ONLY valid raw JSON."
        )
        evaluator = Agent(
            model=load_model(),
            system_prompt="Output strict JSON only. Do not format as markdown."
        )
        full_text = ""
        stream = evaluator.stream_async(eval_prompt)
        async for event in stream:
            if "data" in event and isinstance(event["data"], str):
                chunk = event["data"]
                full_text += chunk
                await websocket.send_json({"type": "text_delta", "content": chunk})

        await websocket.send_json({"type": "done"})
        await websocket.close()
        return

    # --- Standard invocation over WebSocket ---
    prompt_input = payload.get("prompt", "")
    image_b64 = payload.get("image_b64")
    speaker_meta = payload.get("speaker", {})
    channel_id = payload.get("channel_id", "")
    thread_ctx = payload.get("thread_context", {})

    context_block = ""
    if speaker_meta:
        context_block += f"[Speaker: {speaker_meta.get('display_name', 'unknown')}]\n"
    if channel_id:
        context_block += f"[Channel: {channel_id}]\n"
    thread_info = thread_ctx.get("thread", {})
    recent = thread_info.get("recent_messages", [])
    if recent:
        context_block += "[Recent thread messages]\n"
        for m in recent:
            context_block += f"  {m.get('display_name', 'unknown')}: {m.get('text', '')}\n"
    if context_block:
        prompt_input = f"{context_block}\n{prompt_input}"

    global _active_session_id
    _request_context[session_id] = {"channel_id": channel_id, "session_id": session_id, "thread_precontext": context_block}
    _active_session_id = session_id
    log.info(f"[WS REQUEST CONTEXT] Set _request_context for session={session_id}: channel={channel_id}")

    is_new_session = session_id not in _agents
    if is_new_session and channel_id:
        pre_recall_result = pre_recall_channel_memory(prompt_input, channel_id, session_id)
        if pre_recall_result:
            prompt_input = f"[Channel history]\n{pre_recall_result}\n\n{prompt_input}"
            log.info("[WS PRE-RECALL] Injected channel history into prompt context.")

    if image_b64:
        log.info("[WS MODEL CALL] Processing multi-modal input (Text + Image)...")
        prompt_data = [
            {"type": "text", "text": prompt_input or "Please describe and analyze this image."},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}}
        ]
    else:
        log.info(f"[WS MODEL CALL] Invoking main LLM with prompt: '{prompt_input[:60]}...'")
        prompt_data = prompt_input

    # Build tool set — merge agent-namespaced cloud tools with client-namespaced harness tools
    client_tools_schemas = payload.get("client_tools", [])
    extra_tools = []
    if client_tools_schemas:
        extra_tools, _ = build_client_tool_wrappers(client_tools_schemas, websocket)
        log.info(f"[WS TOOLS] Registered {len(extra_tools)} client tools: {[t.tool_name for t in extra_tools]}")

    cloud_tools = TOOL_PROFILES["primary"]()
    all_tools = cloud_tools + extra_tools

    try:
        agent = Agent(
            model=load_model(),
            system_prompt=SYSTEM_PROMPT,
            tools=all_tools,
        )

        stream = agent.stream_async(prompt_data)
        async for event in stream:
            if "data" in event and isinstance(event["data"], str):
                await websocket.send_json({"type": "text_delta", "content": event["data"]})

        await websocket.send_json({"type": "done"})
    except Exception as e:
        log.error(f"[WS ERROR] Agent invocation failed: {e}", exc_info=True)
        try:
            await websocket.send_json({"type": "error", "content": f"Agent error: {e}"})
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


if __name__ == "__main__":
    app.run()
