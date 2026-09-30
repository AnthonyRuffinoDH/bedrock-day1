"""
herocore_bridge.auth
====================
Authentication header resolution helpers.

Extracted from: bot.py (_resolve_env_ref, _resolve_auth_headers)

The original bot.py had auth resolution inlined inside _execute_mcp_tool_http.
This module generalises it so it can be applied to any HTTP server config,
including future servers that need auth headers beyond the MCP executor.
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger("herocore_bridge.auth")

# Pattern that matches ${ENV_VAR_NAME} references inside auth config values.
_ENV_REF_PATTERN = re.compile(r"^\$\{(.+)}$")


def resolve_env_ref(value: str) -> str:
    """Expand a ``${ENV_VAR}`` reference to its environment variable value.

    Parameters
    ----------
    value:
        A string that *may* be an env-var reference in the form ``${VAR}``.
        Plain strings are returned unchanged.

    Returns
    -------
    str
        The resolved value, or the original string if no match.

    Raises
    ------
    EnvironmentError
        If the referenced environment variable is not set.
    """
    match = _ENV_REF_PATTERN.match(value.strip())
    if match:
        var_name = match.group(1)
        resolved = os.environ.get(var_name)
        if resolved is None:
            raise EnvironmentError(f"Environment variable '{var_name}' is not set (required by auth config).")
        return resolved
    return value


def resolve_auth_headers(auth_config: dict | None) -> dict[str, str]:
    """Build HTTP auth headers from an MCP server ``auth`` config block.

    Supported schemes
    -----------------
    ``bearer``
        Adds ``Authorization: Bearer <token>`` where ``<token>`` may be a
        literal value or a ``${ENV_VAR}`` reference.

    ``basic``
        Adds ``Authorization: Basic <b64>`` built from ``username`` and
        ``password`` fields (both support ``${ENV_VAR}`` refs).

    ``header``
        Adds an arbitrary ``<header_name>: <header_value>`` pair.

    Parameters
    ----------
    auth_config:
        The ``auth`` dict from a server entry in ``mcp_tools.json``, or
        *None* / an empty dict if no auth is required.

    Returns
    -------
    dict[str, str]
        Ready-to-use headers dict (may be empty).
    """
    if not auth_config:
        return {}

    import base64  # stdlib, only needed here

    scheme = auth_config.get("type", "").lower()

    try:
        if scheme == "bearer":
            token = resolve_env_ref(auth_config.get("token", ""))
            return {"Authorization": f"Bearer {token}"}

        if scheme == "basic":
            username = resolve_env_ref(auth_config.get("username", ""))
            password = resolve_env_ref(auth_config.get("password", ""))
            encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
            return {"Authorization": f"Basic {encoded}"}

        if scheme == "header":
            name = auth_config.get("name", "")
            value = resolve_env_ref(auth_config.get("value", ""))
            if name:
                return {name: value}
            logger.warning("[AUTH] 'header' auth scheme missing 'name' field — skipping.")
            return {}

        if scheme:
            logger.warning("[AUTH] Unknown auth scheme '%s' — no headers added.", scheme)
        return {}

    except EnvironmentError as exc:
        logger.error("[AUTH] Failed to resolve auth headers: %s", exc)
        return {}
