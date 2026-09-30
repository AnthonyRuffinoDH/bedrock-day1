"""Auth resolution for MCP server configurations."""
from __future__ import annotations

import base64
import logging
import os
import re

logger = logging.getLogger("herocore_bridge.auth")

_ENV_REF_PATTERN = re.compile(r'^\$\{([^}]+)\}$')


def resolve_env_ref(value: str) -> str | None:
    """Resolve a ${VAR} reference from the environment. Returns None if the var is missing."""
    m = _ENV_REF_PATTERN.match(value)
    if not m:
        return value
    return os.environ.get(m.group(1))


def resolve_auth_headers(server: dict) -> tuple[dict, str | None]:
    """Build auth headers from server config. Returns (headers, error_or_none)."""
    auth = server.get("auth")
    if not auth:
        return {}, None

    auth_type = auth.get("type", "")
    server_name = server.get("name", "unknown")

    if auth_type == "bearer":
        token = resolve_env_ref(auth.get("token", ""))
        if token is None:
            return {}, f"Error: environment variable for bearer token not set (server '{server_name}')"
        logger.info(f"[MCP AUTH] Using bearer auth for server '{server_name}'")
        return {"Authorization": f"Bearer {token}"}, None

    elif auth_type == "header":
        key = auth.get("key", "")
        val = resolve_env_ref(auth.get("value", ""))
        if val is None:
            return {}, f"Error: environment variable for header value not set (server '{server_name}')"
        logger.info(f"[MCP AUTH] Using header auth for server '{server_name}'")
        return {key: val}, None

    elif auth_type == "basic":
        username = resolve_env_ref(auth.get("username", ""))
        password = resolve_env_ref(auth.get("password", ""))
        if username is None or password is None:
            return {}, f"Error: environment variable for basic auth not set (server '{server_name}')"
        encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
        logger.info(f"[MCP AUTH] Using basic auth for server '{server_name}'")
        return {"Authorization": f"Basic {encoded}"}, None

    else:
        logger.warning(f"[MCP AUTH] Unknown auth type '{auth_type}' for server '{server_name}', skipping auth")
        return {}, None
