from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from model.load import load_model
from mcp_client.client import get_streamable_http_mcp_client, get_gateway_mcp_client
from memory.session import get_memory_session_manager
import jwt
import logging
import json

app = BedrockAgentCoreApp()
log = app.logger
log.setLevel(logging.INFO)

# MCP clients: Exa AI (web search) + AgentCore Gateway (Lambda tools)
mcp_clients = [get_streamable_http_mcp_client(), get_gateway_mcp_client()]

SYSTEM_PROMPT = """You are a helpful and professional customer support assistant deployed as a Slack bot.

<memory_architecture_and_rules>
You operate with two distinct types of injected memory context:
1. CURRENT THREAD (Session ID): This is the active conversation you are having right now. This is your primary focus.
2. CHANNEL KNOWLEDGE (User ID): You are deployed in a shared Slack channel. The system automatically injects past conversations from OTHER threads in this channel into your background memory.

RULES FOR INTERPRETING MEMORY:
- STRICT RULE: Always treat the user's most recent prompt as a completely NEW topic within the CURRENT THREAD unless they explicitly refer back to something.
- NEVER assume the user wants to continue a topic found in the CHANNEL KNOWLEDGE unless they explicitly ask about it.
- Use CHANNEL KNOWLEDGE only as passive background context.
</memory_architecture_and_rules>

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

tools = [get_return_policy, get_product_info]

for mcp_client in mcp_clients:
    if mcp_client:
        tools.append(mcp_client)

# Dict for caching active thread agents in local container RAM
_agents = {}

def get_or_create_agent(session_id, user_id):
    global _agents

    if session_id not in _agents:
        log.info(
            f"[NEW INSTANCE] Hydrating session={session_id}, actor={user_id}"
        )

        sm = get_memory_session_manager(session_id, user_id)

        agent = Agent(
            model=load_model(),
            session_manager=sm,
            system_prompt=SYSTEM_PROMPT,
            tools=tools,
        )

        _agents[session_id] = agent
    else:
        log.info(
            f"[WARM INSTANCE] Reusing agent for session={session_id}"
        )

    return _agents[session_id]

def extract_user_id(context) -> str | None:
    headers = context.request_headers or {}
    auth_header = headers.get("Authorization") or headers.get("authorization")
    if auth_header and auth_header.startswith("Bearer "):
        try:
            token = auth_header.split(" ", 1)[1]
            claims = jwt.decode(token, options={"verify_signature": False})
            username = claims.get("username")
            if username:
                return username
        except Exception as e:
            log.warning(f"Failed to decode JWT for user_id: {e}")
    return headers.get("x-amzn-bedrock-agentcore-runtime-custom-user-id")

@app.entrypoint
async def invoke(payload, context):
    log.info(f"[AGENTCORE INVOKE] Received payload keys: {list(payload.keys())}")
    log.info(
        "[SESSION DEBUG] "
        f"context.session_id={context.session_id!r} | "
        f"payload.sessionId={payload.get('sessionId')!r}"
    )

    session_id = context.session_id
    user_id = extract_user_id(context)

    if not session_id or not user_id:
        raise ValueError("session_id and user_id are required.")

    action = payload.get("action")

    # --- 1. EVALUATION GATE ---
    if action == "evaluate_gate":
        log.info("[GATE EVALUATION] Running ambient message classification...")
        prompt_text = payload.get("prompt", "")
        eval_prompt = (
            f"You are a channel moderation router.\n"
            f"Analyze this message: \"{prompt_text}\"\n\n"
            f"Rules:\n"
            f"1. If asking for gambling, sports betting advice, or violating policies, output: {{\"action\": \"INAPPROPRIATE\"}}\n"
            f"2. If asking a customer support, product, or policy question the bot SHOULD answer, output: {{\"action\": \"RESPOND\"}}\n"
            f"3. If casual user-to-user chatter or irrelevant conversation, output: {{\"action\": \"IGNORE\"}}\n"
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

    if image_b64:
        log.info("[MODEL CALL] Processing multi-modal input (Text + Image)...")
        prompt_data = [
            {"type": "text", "text": prompt_input or "Please describe and analyze this image."},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}}
        ]
    else:
        log.info(f"[MODEL CALL] Invoking main LLM with prompt: '{prompt_input[:60]}...'")
        prompt_data = prompt_input

    agent = get_or_create_agent(session_id, user_id)
    stream = agent.stream_async(prompt_data)
    async for event in stream:
        if "data" in event and isinstance(event["data"], str):
            yield event["data"]

if __name__ == "__main__":
    app.run()