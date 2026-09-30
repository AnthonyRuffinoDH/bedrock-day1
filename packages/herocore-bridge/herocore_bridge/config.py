"""
herocore_bridge.config
======================
Loads the mcp_tools.json manifest and builds the in-memory tool routing table
used by the executor and the WebSocket client loop.

Extracted from: bot.py (_load_mcp_tools, _tool_to_server)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger("herocore_bridge.config")


def load_mcp_tools(config_path: str | None = None) -> list[dict[str, Any]]:
    """Load local MCP tool schemas from ``mcp_tools.json``.

    Parameters
    ----------
    config_path:
        Explicit path to the JSON file.  If *None* the function looks for
        ``mcp_tools.json`` next to the caller's working directory (i.e. the
        same behaviour as the original ``bot.py`` implementation).

    Returns
    -------
    list[dict]
        A flat list of tool dicts, each augmented with ``_server``,
        ``_command``, ``_transport``, and ``_url`` keys for routing.
        Returns an empty list when the file is absent or malformed.
    """
    if config_path is None:
        config_path = os.path.join(os.getcwd(), "mcp_tools.json")

    if not os.path.exists(config_path):
        logger.info("[MCP CONFIG] No mcp_tools.json found at %s. Starting without client tools.", config_path)
        return []

    try:
        with open(config_path) as f:
            config = json.load(f)
        servers = config.get("servers", [])
        tools: list[dict[str, Any]] = []
        for server in servers:
            server_name = server.get("name", "unknown")
            for t in server.get("tools", []):
                t["_server"] = server_name
                t["_command"] = server.get("command")
                t["_transport"] = server.get("transport", "stdio")
                t["_url"] = server.get("url")
                tools.append(t)
        logger.info("[MCP CONFIG] Loaded %d client tools from %d servers.", len(tools), len(servers))
        return tools
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error("[MCP CONFIG] Failed to parse mcp_tools.json: %s. Starting without client tools.", e)
        return []
    except Exception as e:  # noqa: BLE001
        logger.error("[MCP CONFIG] Unexpected error loading mcp_tools.json: %s. Starting without client tools.", e)
        return []


def build_tool_routing_table(tools: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build a ``tool_name -> server config`` lookup from the tool list.

    Parameters
    ----------
    tools:
        The list returned by :func:`load_mcp_tools`.

    Returns
    -------
    dict[str, dict]
        Mapping of tool name to a server config dict with keys:
        ``name``, ``command``, ``transport``, ``url``.
    """
    return {
        t["name"]: {
            "name": t.get("_server"),
            "command": t.get("_command"),
            "transport": t.get("_transport", "stdio"),
            "url": t.get("_url"),
        }
        for t in tools
    }
