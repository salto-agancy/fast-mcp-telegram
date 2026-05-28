"""/.well-known/mcp/server-card.json endpoint for MCP discovery.

Smithery.ai and similar tools use this well-known path to learn about
available tools, authentication, and capabilities *without* needing to
start a real Telegram session (which requires API_ID / API_HASH).

Tool definitions are extracted from the FastMCP server instance after all
tools are registered, eliminating drift between advertised and actual tools.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from starlette.responses import JSONResponse, Response
from starlette.requests import Request

from src._version import __version__

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register_server_card_route(mcp_app: FastMCP) -> None:
    """Register the ``/.well-known/mcp/server-card.json`` HTTP route.

    Available only in HTTP transport mode.  Smithery.ai and similar tools
    will discover it when connecting to a publicly reachable deployment.

    Must be called *after* ``register_tools(mcp_app)`` so all tools are
    available via ``mcp_app.list_tools()``.  The card is built lazily on
    the first request and then cached for the lifetime of the process.
    """
    _card_cache: dict | None = None

    @mcp_app.custom_route("/.well-known/mcp/server-card.json", methods=["GET"])
    async def server_card(request: Request):
        nonlocal _card_cache

        if _card_cache is None:
            tools = await mcp_app.list_tools()
            _card_cache = {
                "serverInfo": {
                    "name": "fast-mcp-telegram",
                    "version": __version__,
                },
                "authentication": {
                    # Per MCP server card spec: `required = false` means optional.
                    # `schemes` is a list of supported auth scheme identifiers.
                    "required": False,
                    "schemes": ["bearer"],
                },
                "tools": [
                    {
                        "name": t.name,
                        "description": t.description or "",
                        "inputSchema": t.inputSchema,
                    }
                    for t in tools
                ],
                "resources": [],
                "prompts": [],
            }

        etag = _compute_etag(_card_cache)

        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})

        return JSONResponse(
            _card_cache,
            headers={
                "Cache-Control": "public, max-age=3600",
                "ETag": etag,
            },
        )


def _compute_etag(card: dict) -> str:
    """Content-based ETag derived from the JSON representation."""
    raw = json.dumps(card, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return f'"{hashlib.md5(raw, usedforsecurity=False).hexdigest()}"'
