"""
FastMCP middleware that prefixes listed tool names with a per-session account label.

Each authenticated HTTP session sees tool names like ``alice_send_message`` (Telegram
@username when set, otherwise numeric user id), enabling multi-account agents that
connect to one server with different Bearer tokens.
"""

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
from src.config.server_config import get_config

# token -> account label (username or str(user id))
_account_prefix_cache: dict[str, str] = {}


def _prefixed_tool_name(label: str, internal: str) -> str:
    return f"{label}_{internal}"


def _strip_account_prefix(label: str, external: str) -> str | None:
    prefix = f"{label}_"
    if external.startswith(prefix):
        return external[len(prefix) :]
    return None


def _get_cached_account_prefix(token: str) -> str | None:
    label = _account_prefix_cache.get(token)
    if label is None:
        return None
    del _account_prefix_cache[token]
    _account_prefix_cache[token] = label
    return label


def _cache_account_prefix(token: str, label: str) -> None:
    max_size = get_config().max_active_sessions
    if token in _account_prefix_cache:
        del _account_prefix_cache[token]
    elif len(_account_prefix_cache) >= max_size:
        oldest = next(iter(_account_prefix_cache))
        del _account_prefix_cache[oldest]
    _account_prefix_cache[token] = label


def clear_account_prefix_cache() -> None:
    """Clear the account-prefix cache (for tests)."""
    _account_prefix_cache.clear()


async def _resolve_account_prefix(session_token: str) -> str | None:
    """Return the tool-name prefix label for *session_token*, fetching and caching it."""
    cached = _get_cached_account_prefix(session_token)
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
        label = (username or "").strip() or str(me.id)
        _cache_account_prefix(session_token, label)
        return label
    finally:
        set_request_token(saved)


class AccountPrefixedToolsMiddleware(Middleware):
    """Prefix every tool name with the current session's Telegram account label."""

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        tools = await call_next(context)

        token = get_access_token()
        if token is None:
            return tools

        label = await _resolve_account_prefix(token.token)
        if not label:
            return tools

        return [
            tool.model_copy(update={"name": _prefixed_tool_name(label, tool.name)})
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

        label = await _resolve_account_prefix(token.token)
        if not label:
            return await call_next(context)

        internal_name = _strip_account_prefix(label, context.message.name)
        if internal_name is None:
            return await call_next(context)

        modified = context.copy(
            message=mt.CallToolRequestParams(
                name=internal_name,
                arguments=context.message.arguments,
            )
        )
        return await call_next(modified)
