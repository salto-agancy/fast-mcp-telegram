"""
FastMCP middleware that prefixes listed tool names with a per-session account label.

Each authenticated HTTP session sees tool names like ``alice_send_message`` (Telegram
@username when set, otherwise numeric user id), enabling multi-account agents that
connect to one server with different Bearer tokens.
"""

from collections.abc import Generator, Sequence
from contextlib import contextmanager

import mcp.types as mt
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import Tool, ToolResult

from src.client.connection import (
    get_connected_client,
    get_request_token,
    set_request_token,
)
from src.server_components.account_prefix_cache import (
    _account_prefix_cache,
    clear_account_prefix_cache,
)

__all__ = ["AccountPrefixedToolsMiddleware", "clear_account_prefix_cache"]


def _prefixed_tool_name(label: str, internal: str) -> str:
    return f"{label}_{internal}"


def _strip_account_prefix(label: str, external: str) -> str | None:
    prefix = f"{label}_"
    return external[len(prefix) :] if external.startswith(prefix) else None


@contextmanager
def _session_request_token(session_token: str) -> Generator[None, None, None]:
    saved = get_request_token()
    set_request_token(session_token)
    try:
        yield
    finally:
        set_request_token(saved)


async def _resolve_account_prefix(session_token: str) -> str | None:
    """Return the tool-name prefix label for *session_token*, fetching and caching it."""
    if _account_prefix_cache.is_unresolved(session_token):
        return None

    cached = _account_prefix_cache.get(session_token)
    if cached is not None:
        return cached

    with _session_request_token(session_token):
        client = await get_connected_client()
        me = await client.get_me()

    if me is None:
        _account_prefix_cache.remember_unresolved(session_token)
        return None

    username = getattr(me, "username", None)
    label = (username or "").strip() or str(me.id)
    _account_prefix_cache.put(session_token, label)
    return label


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
            raise ToolError(
                f"Tool name must use account prefix '{label}_' "
                f"(got {context.message.name!r})"
            )

        modified = context.copy(
            message=context.message.model_copy(update={"name": internal_name})
        )
        return await call_next(modified)
