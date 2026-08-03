"""Rich message sending (Telegram Bot API 10.1 `sendRichMessage`).

Renders markdown with REAL headings and tables in Telegram (like the night
autopilot reports), which a user session cannot do — `sendRichMessage` is a
BOT method. So this tool is deliberately decoupled from the server's Telethon
user session: it converts markdown -> Rich HTML (via markdown-it-py, already in
the image) and POSTs to the Bot API using a SEPARATE bot token (`RICH_BOT_TOKEN`
= @salto_claude_channel_bot). It never touches `get_connected_client`, so it
does not turn the server into a bot session and bot_restrictions leave it alone.

Because a bot can only write to a chat it shares with the user (not the user's
Saved Messages), the caller must pass the bot-DM chat_id (Dima: 774060215),
not "me".
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx
from markdown_it import MarkdownIt

logger = logging.getLogger(__name__)

_BOT_API = "https://api.telegram.org"
_RICH_TOKEN_ENV = "RICH_BOT_TOKEN"


def markdown_to_rich_html(md_text: str) -> str:
    """Convert markdown to the Rich-HTML tag set accepted by sendRichMessage.

    Full-markdown v2 (verified against live sendRichMessage probes msg 533-538):
    - gfm-like preset -> tables, strikethrough, autolinks, nested lists, ol,
      code blocks, blockquote
    - html:True passes inline Telegram tags through: <tg-emoji emoji-id=...>,
      <tg-spoiler>, <u> (custom emoji works when the bot owner has Premium)
    - ||text|| -> <tg-spoiler>; GFM task-list `- [ ]`/`- [x]` -> visible ☐/☑
      boxes (Rich does not build an interactive checkbox from GFM)
    - normalize to reference tag set: strong->b, em->i, del->s, drop thead/tbody,
      collapse whitespace between tags
    """
    # inline transforms before render
    md_text = re.sub(r"\|\|(.+?)\|\|", r"<tg-spoiler>\1</tg-spoiler>", md_text)
    md_text = re.sub(r"(?m)^(\s*[-*]) \[[ ]\] ", r"\1 ☐ ", md_text)
    md_text = re.sub(r"(?m)^(\s*[-*]) \[[xX]\] ", r"\1 ☑ ", md_text)

    # commonmark base (not gfm-like: that enables linkify which needs linkify-it-py,
    # absent in the server image); table+strikethrough enabled explicitly
    html = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"]).render(md_text)

    html = re.sub(r"</?(thead|tbody)>", "", html)
    html = html.replace("<strong>", "<b>").replace("</strong>", "</b>")
    html = html.replace("<em>", "<i>").replace("</em>", "</i>")
    html = html.replace("<del>", "<s>").replace("</del>", "</s>")
    html = re.sub(r">\s+<", "><", html)  # collapse whitespace between tags
    return html.strip()


async def send_rich_message_impl(
    chat_id: str | int,
    markdown: str,
    reply_to_id: int | None = None,
) -> dict[str, Any]:
    """Send markdown as a Telegram Rich Message (tables/headings) via the bot.

    Returns {sent, id, chat_id, type} on success; raises ValueError with a clear
    message on misconfig/API error so the web skill can fall back to HTML.
    """
    token = os.environ.get(_RICH_TOKEN_ENV)
    if not token:
        raise ValueError(
            f"{_RICH_TOKEN_ENV} is not set on the server; rich messages need the "
            "@salto_claude_channel_bot token. Falling back to plain send_message is advised."
        )
    if not markdown or not markdown.strip():
        raise ValueError("markdown is empty")

    html = markdown_to_rich_html(markdown)
    payload: dict[str, Any] = {"chat_id": chat_id, "rich_message": {"html": html}}
    if reply_to_id is not None:
        payload["reply_parameters"] = {"message_id": reply_to_id}

    url = f"{_BOT_API}/bot{token}/sendRichMessage"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json=payload)
    data = resp.json()
    if not data.get("ok"):
        raise ValueError(f"sendRichMessage failed: {data.get('description')}")

    result = data.get("result", {})
    return {
        "sent": True,
        "id": result.get("message_id"),
        "chat_id": chat_id,
        "type": "rich",
    }
