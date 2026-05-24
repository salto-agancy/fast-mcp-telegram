"""
Middleware that dynamically prefixes MCP tool names with the connected Telegram
account's username.  Each authenticated session sees its own tool names
(e.g. ``@{alice}_send_message``), enabling multi-user session isolation over HTTP.
"""

import logging
from collections.abc import Sequence

import mcp.types as mt
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import Tool, ToolResult

from src.client.connection import (
    get_connected_client,
    get_request_token,
    set_request_token,
)

logger = logging.getLogger(__name__)

# Per-session username cache: token -> username
_username_cache: dict[str, str] = {}


async def _resolve_username(session_token: str) -> str | None:
    """Return the Telegram username for *session_token*, fetching and caching it."""
    cached = _username_cache.get(session_token)
    if cached is not None:
        return cached

    saved = get_request_token()
    set_request_token(session_token)
    try:
        client = await get_connected_client()
        me = await client.get_me()
        if me is None:
            return None
        username = getattr(me, "username", None)
        if username:
            _username_cache[session_token] = username
            return username
        return None
    except Exception:
        logger.warning("Could not resolve username for token %s...", session_token[:8])
        return None
    finally:
        set_request_token(saved)


class UsernameToolMiddleware(Middleware):
    """Prefix every tool name with the current session's Telegram username."""

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        tools = await call_next(context)

        token = get_access_token()
        if token is None:
            return tools

        username = await _resolve_username(token.token)
        if not username:
            return tools

        return [
            tool.model_copy(update={"name": f"{username}_{tool.name}"})
            for tool in tools
        ]

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        token = get_access_token()
        if token is None:
            return await call_next(context)

        username = await _resolve_username(token.token)
        if not username:
            return await call_next(context)

        external_name = context.message.name
        prefix = f"{username}_"
        if external_name.startswith(prefix):
            internal_name = external_name[len(prefix) :]
            modified = context.copy(
                message=mt.CallToolRequestParams(
                    name=internal_name,
                    arguments=context.message.arguments,
                )
            )
            return await call_next(modified)

        return await call_next(context)
