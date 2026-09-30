from strands import Agent, tool
from strands.types.tools import AgentTool, ToolUse, ToolSpec, ToolResult
from strands.types._events import ToolResultEvent
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from starlette.websockets import WebSocketDisconnect
from model.load import load_model
from mcp_client.client import get_streamable_http_mcp_client, get_gateway_mcp_client
from memory.session import get_channel_memory_session_manager
import asyncio
import logging
import json
import os
import uuid
from typing import Any
from datetime import datetime, timezone

app = BedrockAgentCoreApp()
log = app.logger
log.setLevel(logging.INFO)

# MCP clients: Exa AI (web search) + AgentCore Gateway (Lambda tools)
mcp_clients = [get_streamable_http_mcp_client(), get_gateway_mcp_client()]

SYSTEM_PROMPT = """You are a helpful and professional customer support assistant deployed as a Slack bot.

<conversational_context>
Your primary source of conversational context is the recent thread messages included with each request. Use these to understand the ongoing conversation. You do not automatically have access to persistent memory.
</conversational_context>

<memory_tools>
You have two optional tools for persistent channel memory:
- channel_memory_store: Save information that will be useful in future conversations in this channel. Use only for durably valuable knowledge (e.g., recurring issues, team preferences, important decisions).
- channel_memory_recall: Query previously stored channel knowledge when prior context would materially improve your response.

Guidelines:
- Do NOT store routine support interactions. Only store information likely to remain useful beyond the current thread.
- When storing updates or resolutions to previously stored facts, always include the original topic and prior status in the content so future searches find the update. For example: "RESOLVED: The stale product catalog issue originally reported as ongoing is now fixed" or "UPDATED: Team return policy preference changed from store credit to full refund."
- When you recall memory, treat it as supporting context, not authoritative. The active thread is always the primary source of truth.
- If recalled memory conflicts with what is visible in the current thread, trust the thread.
- Memory is optional. If a memory operation fails, continue the conversation normally.
</memory_tools>

<memory_reconciliation>
IMPORTANT — you MUST follow these rules when using recalled memory:
- If multiple entries about the same topic have contradictory statuses, treat the entry indicating resolution/completion/update as authoritative. Do NOT present an issue as ongoing when a resolution exists.
- You MUST NOT present recalled information as definitively current. Always qualify with temporal language: "As of the last update I have...", "The most recent record shows...", or "Based on what was previously stored...". Memory entries may be outdated.
- Never state "there IS an active issue" based solely on a recalled entry. Instead say "the last update I have indicates..." or similar.
- If only one entry is returned about an issue, explicitly note that you cannot confirm whether it is still current.
</memory_reconciliation>

<support_guidelines>
Your role is to:
- Provide accurate information using the tools available to you.
- Be friendly, patient, and understanding with customers.
- Always use tools to get accurate, up-to-date information rather than guessing.
</support_guidelines>"""

# --- Customer Support Tools ---

RETURN_POLICIES = {
    "electronics": {"window": "30 days", "condition": "Original packaging required, must be unused or defective", "refund": "Full refund to original payment method"},
    "accessories": {"window": "14 days", "condition": "Must be in original packaging, unused", "refund": "Store credit or exchange"},
    "audio": {"window": "30 days", "condition": "Defective items only after 15 days", "refund": "Full refund within 15 days, replacement after"},
}

PRODUCTS = {
    "PROD-001": {"name": "Wireless Headphones", "price": 79.99, "category": "audio", "description": "Noise-cancelling Bluetooth headphones with 30h battery life", "warranty_months": 12},
    "PROD-002": {"name": "Smart Watch", "price": 249.99, "category": "electronics", "description": "Fitness tracker with heart rate monitor, GPS, and 5-day battery", "warranty_months": 24},
    "PROD-003": {"name": "Laptop Stand", "price": 39.99, "category": "accessories", "description": "Adjustable aluminum laptop stand for ergonomic desk setup", "warranty_months": 6},
    "PROD-004": {"name": "USB-C Hub", "price": 54.99, "category": "accessories", "description": "7-in-1 USB-C hub with HDMI, USB-A, SD card reader, and ethernet", "warranty_months": 12},
    "PROD-005": {"name": "Mechanical Keyboard", "price": 129.99, "category": "electronics", "description": "RGB mechanical keyboard with Cherry MX switches", "warranty_months": 24},
}

@tool
def get_return_policy(product_category: str) -> str:
    """Get return policy information for a specific product category."""
    log.info(f"[TOOL EXECUTION] get_return_policy called with category: '{product_category}'")
    category = product_category.lower()
    if category in RETURN_POLICIES:
        policy = RETURN_POLICIES[category]
        return f"Return policy for {category}: Window: {policy['window']}, Condition: {policy['condition']}, Refund: {policy['refund']}"
    return f"No specific return policy found for '{product_category}'. Please contact support for details."

@tool
def get_product_info(query: str) -> str:
    """Search for product information by name, ID, or keyword."""
    log.info(f"[TOOL EXECUTION] get_product_info called with query: '{query}'")
    query_lower = query.lower()
    if query.upper() in PRODUCTS:
        p = PRODUCTS[query.upper()]
        return f"{p['name']} ({query.upper()}): ${p['price']}, Category: {p['category']}, {p['description']}, Warranty: {p['warranty_months']} months"
    results = [f"{pid}: {p['name']} - ${p['price']} - {p['description']}" for pid, p in PRODUCTS.items()
               if query_lower in p['name'].lower() or query_lower in p['description'].lower() or query_lower in p['category'].lower()]
    if results:
        return "Found products:\n" + "\n".join(results)
    return f"No products found matching '{query}'."

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


@tool
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


@tool
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


_domain_tools = [get_return_policy, get_product_info]
_mcp_tools = [c for c in mcp_clients if c]
_memory_tools = [channel_memory_store, channel_memory_recall]

TOOL_PROFILES = {
    "primary": lambda: _domain_tools + _mcp_tools + _memory_tools,
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
            f"1. If the message requests gambling, sports betting advice, or violates policies → {{\"action\": \"INAPPROPRIATE\"}}\n"
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
    _request_context[session_id] = {"channel_id": channel_id, "session_id": session_id}
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

WS_TOOL_TIMEOUT = int(os.environ.get("WS_TOOL_TIMEOUT", "30"))


class _WebSocketProxyTool(AgentTool):
    """A tool that proxies calls over WebSocket to the harness for local MCP execution."""

    def __init__(self, name: str, description: str, input_schema: dict, websocket):
        super().__init__()
        self._name = name
        self._description = description
        self._input_schema = input_schema
        self._websocket = websocket

    @property
    def tool_name(self) -> str:
        return self._name

    @property
    def tool_spec(self) -> ToolSpec:
        return {
            "name": self._name,
            "description": self._description,
            "inputSchema": {"json": self._input_schema},
        }

    @property
    def tool_type(self) -> str:
        return "function"

    async def stream(self, tool_use: ToolUse, invocation_state: dict[str, Any], **kwargs: Any):
        tool_use_id = tool_use.get("toolUseId", "unknown")
        tool_input = tool_use.get("input", {})
        call_id = str(uuid.uuid4())

        log.info(f"[WS TOOL PROXY] Sending tool_call: {self._name} (id={call_id})")
        try:
            await self._websocket.send_json({
                "type": "tool_call",
                "tool_call_id": call_id,
                "name": self._name,
                "args": tool_input,
            })
            result_msg = await asyncio.wait_for(
                self._websocket.receive_json(), timeout=WS_TOOL_TIMEOUT
            )
        except asyncio.TimeoutError:
            log.warning(f"[WS TOOL PROXY] Timeout waiting for tool_result: {self._name} (id={call_id})")
            yield ToolResultEvent({"toolUseId": tool_use_id, "status": "error", "content": [{"text": f"Error: tool execution timed out after {WS_TOOL_TIMEOUT}s"}]})
            return
        except WebSocketDisconnect:
            log.warning(f"[WS TOOL PROXY] Client disconnected: {self._name} (id={call_id})")
            yield ToolResultEvent({"toolUseId": tool_use_id, "status": "error", "content": [{"text": "Error: client disconnected during tool execution"}]})
            return
        except Exception as e:
            log.error(f"[WS TOOL PROXY] Error proxying tool {self._name}: {e}")
            yield ToolResultEvent({"toolUseId": tool_use_id, "status": "error", "content": [{"text": f"Error: {e}"}]})
            return

        if result_msg.get("type") != "tool_result" or result_msg.get("tool_call_id") != call_id:
            log.warning(f"[WS TOOL PROXY] Unexpected message while awaiting tool_result: {result_msg}")
            yield ToolResultEvent({"toolUseId": tool_use_id, "status": "error", "content": [{"text": "Error: unexpected response from harness"}]})
            return

        content = result_msg.get("content", "")
        log.info(f"[WS TOOL PROXY] Received tool_result for {self._name} (id={call_id})")
        yield ToolResultEvent({"toolUseId": tool_use_id, "status": "success", "content": [{"text": content}]})


def _build_client_tool_wrappers(client_tools, websocket):
    """Create AgentTool instances that proxy calls over the WebSocket."""
    wrappers = []
    client_tool_names = set()

    for schema in client_tools:
        tool_name = schema["name"]
        tool_desc = schema.get("description", "")
        input_schema = schema.get("input_schema", {"type": "object", "properties": {}})
        client_tool_names.add(tool_name)
        wrappers.append(_WebSocketProxyTool(tool_name, tool_desc, input_schema, websocket))

    return wrappers, client_tool_names


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
            f"1. If the message requests gambling, sports betting advice, or violates policies → {{\"action\": \"INAPPROPRIATE\"}}\n"
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
    _request_context[session_id] = {"channel_id": channel_id, "session_id": session_id}
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

    # Build tool set — merge cloud tools with any client tools from payload
    client_tools_schemas = payload.get("client_tools", [])
    extra_tools = []
    if client_tools_schemas:
        extra_tools, _ = _build_client_tool_wrappers(client_tools_schemas, websocket)
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