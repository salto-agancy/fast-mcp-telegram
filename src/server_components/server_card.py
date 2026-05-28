"""/.well-known/mcp/server-card.json endpoint for Smithery.ai and MCP discovery.

When scanned by Smithery.ai, the server serves static tool metadata at this
well-known path so that Smithery can learn about available tools, resources,
and authentication requirements *without* needing to start a real Telegram
session (which requires API_ID / API_HASH credentials).

See https://smithery.ai/docs/build/publish#static-server-card
"""

import json
from pathlib import Path

from starlette.responses import JSONResponse


def _load_server_card() -> dict:
    """Load the static server-card.json shipped with the package."""
    card_path = Path(__file__).resolve().parent.parent.parent / ".well-known" / "mcp" / "server-card.json"
    with open(card_path, encoding="utf-8") as f:
        return json.load(f)


_CARD: dict | None = None


def get_server_card() -> dict:
    """Return the server card dict (lazy-loaded singleton)."""
    global _CARD
    if _CARD is None:
        _CARD = _load_server_card()
    return _CARD


def register_server_card_route(mcp_app):
    """Register the ``/.well-known/mcp/server-card.json`` HTTP route.

    The route is registered on the FastMCP Starlette app, so it is available
    only in HTTP transport mode.  Smithery.ai will discover it when connecting
    to a publicly reachable deployment.
    """

    @mcp_app.custom_route("/.well-known/mcp/server-card.json", methods=["GET"])
    async def server_card(request):
        return JSONResponse(get_server_card())
