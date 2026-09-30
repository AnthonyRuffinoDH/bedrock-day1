"""MCP tool configuration loading and routing table construction."""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger("herocore_bridge.config")


def load_mcp_tools(config_path: str) -> list[dict]:
    """Load local MCP tool schemas from a JSON config file. Returns list of tool dicts."""
    if not os.path.exists(config_path):
        logger.info("[MCP CONFIG] No config file found at %s. Starting without client tools.", config_path)
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
                t["_auth"] = server.get("auth")
                tools.append(t)
        logger.info(f"[MCP CONFIG] Loaded {len(tools)} client tools from {len(servers)} servers.")
        return tools
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error(f"[MCP CONFIG] Failed to parse config: {e}. Starting without client tools.")
        return []
    except Exception as e:
        logger.error(f"[MCP CONFIG] Unexpected error loading config: {e}. Starting without client tools.")
        return []


def build_tool_routing_table(tools: list[dict]) -> dict[str, dict]:
    """Build a lookup table: tool_name -> server config for routing."""
    table = {}
    for t in tools:
        table[t["name"]] = {
            "name": t.get("_server"),
            "command": t.get("_command"),
            "transport": t.get("_transport", "stdio"),
            "url": t.get("_url"),
            "auth": t.get("_auth"),
        }
    return table
