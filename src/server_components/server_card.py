"""/.well-known/mcp/server-card.json endpoint for MCP discovery.

Smithery.ai and similar tools use this well-known path to learn about
available tools, authentication, and capabilities *without* needing to
start a real Telegram session (which requires API_ID / API_HASH).

The card data is embedded directly in this module to avoid file I/O at
runtime and to work reliably when the package is installed from PyPI
(via uv, pip, etc.).

Tool entries below (``_CARD_TOOLS``) are the single source of truth for
the discovery card.  Keep them in sync with the actual tool registrations
in ``src.server_components.tools_register``.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache

from starlette.responses import JSONResponse

from src._version import __version__

# ---------------------------------------------------------------------------
# Tool card entries — single source of truth for the discovery card.
# When adding or changing tools in tools_register.py update this list too.
# ---------------------------------------------------------------------------

_CARD_TOOLS: list[dict] = [
            {
                "name": "search_messages_globally",
                "description": "Search all Telegram chats at once. Comma-separated query terms; optional filters by date, chat kind, and public username. Global search ignores include_total_count.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search terms, comma-separated for multiple terms (OR-style global search). Required.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum messages to return (recommended 50 or less).",
                            "default": 50,
                        },
                        "min_date": {
                            "type": "string",
                            "description": "Inclusive minimum date filter (ISO 8601 date or datetime). Omit for no lower bound.",
                        },
                        "max_date": {
                            "type": "string",
                            "description": "Inclusive maximum date filter (ISO 8601 date or datetime). Omit for no upper bound.",
                        },
                        "chat_type": {
                            "type": "string",
                            "description": "Comma-separated chat kinds: private, bot, group, channel. Case-insensitive; extra spaces allowed.",
                        },
                        "public": {
                            "type": "boolean",
                            "description": "If true, prefer chats with a public username; if false, without. Does not apply to private DMs. Omit to skip this filter.",
                        },
                        "auto_expand_batches": {
                            "type": "integer",
                            "description": "Extra search batches to run when filters narrow results. Higher values may return more matches at the cost of latency.",
                            "default": 2,
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "get_messages",
                "description": "Read or search messages in one chat: browse latest, search text, fetch by ids, or load replies to a message (comments, forum topics, threads). Do not combine message_ids with query or reply_to_id.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "chat_id": {
                            "type": "string",
                            "description": "Target chat: numeric id (e.g. -100\u2026), username without @, or 'me' for Saved Messages.",
                        },
                        "query": {
                            "type": "string",
                            "description": "Search within this chat only; comma-separated terms. Omit to browse latest or use message_ids / reply_to_id modes.",
                        },
                        "message_ids": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "Exact message ids to fetch. Mutually exclusive with query and reply_to_id.",
                        },
                        "reply_to_id": {
                            "type": "integer",
                            "description": "Anchor message id: channel post id, forum topic_id from get_chat_info, or a message id for direct replies. Use with thread_scope.",
                        },
                        "thread_scope": {
                            "type": "string",
                            "enum": ["auto", "full", "direct"],
                            "description": "Only with reply_to_id. auto: full forum topic (topic_id) or channel comment thread via getReplies; else direct replies.",
                            "default": "auto",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum messages to return (recommended 50 or less).",
                            "default": 50,
                        },
                        "min_date": {
                            "type": "string",
                            "description": "Inclusive minimum date filter (ISO 8601 date or datetime). Omit for no lower bound.",
                        },
                        "max_date": {
                            "type": "string",
                            "description": "Inclusive maximum date filter (ISO 8601 date or datetime). Omit for no upper bound.",
                        },
                        "auto_expand_batches": {
                            "type": "integer",
                            "description": "Extra search batches to run when filters narrow results. Higher values may return more matches at the cost of latency.",
                            "default": 2,
                        },
                        "include_total_count": {
                            "type": "boolean",
                            "description": "If true, response may include total_count where supported (per-chat search; ignored for global search).",
                            "default": False,
                        },
                    },
                    "required": ["chat_id"],
                },
            },
            {
                "name": "send_message",
                "description": "Send text and optional attachments to a chat.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "chat_id": {
                            "type": "string",
                            "description": "Target chat: numeric id (e.g. -100\u2026), username without @, or 'me' for Saved Messages.",
                        },
                        "message": {
                            "type": "string",
                            "description": "Message text. When sending files, used as caption.",
                        },
                        "reply_to_id": {
                            "type": "integer",
                            "description": "Telegram message id to reply to. For forums, topic root id; for channel posts, post id (may create a comment). Omit for a new top-level message.",
                        },
                        "parse_mode": {
                            "type": "string",
                            "enum": ["markdown", "html", "auto"],
                            "description": "'markdown', 'html', or 'auto' (detect from content). Default is 'auto'.",
                            "default": "auto",
                        },
                        "files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of attachment URLs or local paths (one or more strings). Local paths work in stdio mode only.",
                        },
                    },
                    "required": ["chat_id", "message"],
                },
            },
            {
                "name": "edit_message",
                "description": "Replace text of an existing message you can edit in this chat.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "chat_id": {
                            "type": "string",
                            "description": "Target chat: numeric id (e.g. -100\u2026), username without @, or 'me' for Saved Messages.",
                        },
                        "message_id": {
                            "type": "integer",
                            "description": "Message id in this chat to edit.",
                        },
                        "message": {
                            "type": "string",
                            "description": "New message text.",
                        },
                        "parse_mode": {
                            "type": "string",
                            "enum": ["markdown", "html", "auto"],
                            "description": "'markdown', 'html', or 'auto' (detect from content). Default is 'auto'.",
                            "default": "auto",
                        },
                    },
                    "required": ["chat_id", "message_id", "message"],
                },
            },
            {
                "name": "find_chats",
                "description": "Find users/groups/channels by name, username, or phone. Global search (query required) searches all Telegram; with min_date, max_date, or filter, search uses dialog list or a named filter.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Name, username (no @), phone (+country\u2026), or comma-separated multi-queries. Required for global search unless you use min_date/max_date or folder alone.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum chats to return (recommended 50 or less).",
                            "default": 20,
                        },
                        "chat_type": {
                            "type": "string",
                            "description": "Comma-separated chat kinds: private, bot, group, channel. Case-insensitive; extra spaces allowed.",
                        },
                        "public": {
                            "type": "boolean",
                            "description": "If true, prefer chats with a public username; if false, without. Does not apply to private DMs. Omit to skip this filter.",
                        },
                        "min_date": {
                            "type": "string",
                            "description": "Inclusive minimum date filter (ISO 8601 date or datetime). Omit for no lower bound.",
                        },
                        "max_date": {
                            "type": "string",
                            "description": "Inclusive maximum date filter (ISO 8601 date or datetime). Omit for no upper bound.",
                        },
                        "folder": {
                            "type": "string",
                            "description": "Telegram folder name (case-insensitive exact match after normalization).",
                        },
                    },
                },
            },
            {
                "name": "get_chat_info",
                "description": "Load profile and metadata for one user, bot, group, or channel. Forum chats may include topics up to topics_limit.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "chat_id": {
                            "type": "string",
                            "description": "Target chat: numeric id (e.g. -100\u2026), username without @, or 'me' for Saved Messages.",
                        },
                        "topics_limit": {
                            "type": "integer",
                            "description": "Max forum topics to list when the chat is a forum.",
                            "default": 20,
                        },
                    },
                    "required": ["chat_id"],
                },
            },
            {
                "name": "send_message_to_phone",
                "description": "Send to a phone number: may create a temporary contact, then send text or files.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "phone_number": {
                            "type": "string",
                            "description": "E.164 phone number with country code, e.g. +1234567890 (must be on Telegram).",
                        },
                        "message": {
                            "type": "string",
                            "description": "Message text. When sending files, used as caption.",
                        },
                        "first_name": {
                            "type": "string",
                            "description": "First name when creating a temporary contact.",
                            "default": "Contact",
                        },
                        "last_name": {
                            "type": "string",
                            "description": "Last name when creating a temporary contact.",
                            "default": "Name",
                        },
                        "remove_if_new": {
                            "type": "boolean",
                            "description": "If true, delete the contact after send when it was created only for this send.",
                            "default": False,
                        },
                        "reply_to_msg_id": {
                            "type": "integer",
                            "description": "Reply to this message id in the target chat after resolve.",
                        },
                        "parse_mode": {
                            "type": "string",
                            "enum": ["markdown", "html", "auto"],
                            "description": "'markdown', 'html', or 'auto' (detect from content). Default is 'auto'.",
                            "default": "auto",
                        },
                        "files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of attachment URLs or local paths (one or more strings). Local paths work in stdio mode only.",
                        },
                    },
                    "required": ["phone_number", "message"],
                },
            },
            {
                "name": "invoke_mtproto",
                "description": "Low-level Telegram API (MTProto) invoke for methods not wrapped by other tools. Dangerous methods require allow_dangerous=true.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "method_full_name": {
                            "type": "string",
                            "description": 'Telegram API method, e.g. "messages.GetHistory" or "users.GetFullUser" (normalization applied).',
                        },
                        "params_json": {
                            "type": "string",
                            "description": 'JSON object string of TL parameters as in Telegram API docs; nested TL uses "_": "typeName" discriminator.',
                        },
                        "allow_dangerous": {
                            "type": "boolean",
                            "description": "If false, destructive methods (e.g. deletes) are blocked. Set true only when intended.",
                            "default": False,
                        },
                        "resolve": {
                            "type": "boolean",
                            "description": "If true, resolve string/int peer-like fields to TL Input* entities before invoke.",
                            "default": True,
                        },
                    },
                    "required": ["method_full_name", "params_json"],
                },
            },
    ]


def _build_card() -> dict:
    """Construct the full server card dict with the current package version."""
    return {
        "serverInfo": {
            "name": "fast-mcp-telegram",
            "version": __version__,
        },
        "authentication": {
            "required": False,
            "schemes": ["bearer"],
        },
        "tools": _CARD_TOOLS,
        "resources": [],
        "prompts": [],
    }


@lru_cache(maxsize=1)
def _etag() -> str:
    """Content-based ETag: MD5 of the canonical JSON representation."""
    card = get_server_card()
    raw = json.dumps(card, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.md5(raw).hexdigest()


@lru_cache(maxsize=1)
def get_server_card() -> dict:
    """Return the server card dict (cached after first call).

    The result is cached because ``__version__`` is stable for the lifetime
    of the process.
    """
    return _build_card()


def register_server_card_route(mcp_app):
    """Register the ``/.well-known/mcp/server-card.json`` HTTP route.

    Available only in HTTP transport mode.  Smithery.ai and similar tools
    will discover it when connecting to a publicly reachable deployment.
    """

    @mcp_app.custom_route("/.well-known/mcp/server-card.json", methods=["GET"])
    async def server_card(_):
        return JSONResponse(
            get_server_card(),
            headers={
                "Cache-Control": "public, max-age=3600",
                "ETag": _etag(),
            },
        )
