"""Inline media content for web MCP clients.

`get_messages` returns photos/documents as `attachment_download_url` on the public
origin. Some MCP hosts (e.g. claude.ai web) cannot fetch that URL (sandbox proxy 403,
web_fetch rejects non-search links), so screenshots sent in Telegram stay unreadable.

This module mirrors the voice pattern (`transcribe_voice_messages` inlines text into
the message dict): here we download image bytes server-side, downscale them, and return
them as NATIVE MCP image content (base64) INSIDE the tool result, no URL needed.

Non-image files (xlsx/docx/md/...) return a short text note plus their existing
download URL. Voice / round video return a note pointing at get_messages transcription.
"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import Any

from fastmcp.utilities.types import File, Image

from src.client.connection import get_connected_client
from src.utils.entity import get_entity_by_id
from src.utils.message_format import (
    _build_media_placeholder,
    _document_voice_and_round_note_flags,
    build_message_result,
)

logger = logging.getLogger(__name__)

# Limits (hard-coded by design; see plan open-questions).
MAX_IMAGES = 6
MAX_ORIGINAL_BYTES = 5 * 1024 * 1024
MAX_SIDE = 1600
JPEG_QUALITY = 85
MAX_PDF_BYTES = 20 * 1024 * 1024  # base64 inflates the response; larger → link only


def _document_filename(document) -> str:
    """Filename from a Telethon document's DocumentAttributeFilename, or ''."""
    for attr in getattr(document, "attributes", []) or []:
        if attr.__class__.__name__ == "DocumentAttributeFilename":
            return getattr(attr, "file_name", "") or ""
    return ""


def _classify_media(message) -> str:
    """Classify a Telethon message's media for inlining.

    Returns one of: 'image', 'pdf', 'voice', 'round_video', 'other', 'none'.
    """
    media = getattr(message, "media", None)
    if not media:
        return "none"
    cls = media.__class__.__name__
    if cls == "MessageMediaPhoto":
        return "image"
    if cls == "MessageMediaDocument":
        document = getattr(media, "document", None)
        if not document:
            return "other"
        is_voice, is_round_video = _document_voice_and_round_note_flags(document)
        if is_voice:
            return "voice"
        if is_round_video:
            return "round_video"
        mime = getattr(document, "mime_type", None) or ""
        if isinstance(mime, str) and mime.startswith("image/"):
            return "image"
        if mime == "application/pdf" or _document_filename(document).lower().endswith(
            ".pdf"
        ):
            return "pdf"
        return "other"
    if cls == "MessageMediaVoice":
        return "voice"
    return "other"


def _approx_size_bytes(message) -> int | None:
    """Best-effort original size from the lightweight media placeholder."""
    placeholder = _build_media_placeholder(message) or {}
    size = placeholder.get("approx_size_bytes")
    return size if isinstance(size, int) else None


def _downscale_to_jpeg(raw: bytes) -> bytes:
    """Open image bytes, downscale longest side to MAX_SIDE, re-encode as JPEG."""
    from PIL import Image as PILImage

    with PILImage.open(BytesIO(raw)) as img:
        img = img.convert("RGB")
        if max(img.size) > MAX_SIDE:
            img.thumbnail((MAX_SIDE, MAX_SIDE))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY)
        return buf.getvalue()


async def _file_note(client, message, entity, msg_id: int) -> str:
    """Text note for a non-image document: filename/mime + existing download URL."""
    placeholder = _build_media_placeholder(message) or {}
    filename = placeholder.get("filename") or f"file_{msg_id}"
    mime = placeholder.get("mime_type") or "application/octet-stream"
    url = None
    try:
        # Reuse the canonical URL-minting path (do not duplicate ticket logic).
        built = await build_message_result(client, message, entity, None)
        media = built.get("media")
        if isinstance(media, dict):
            url = media.get("attachment_download_url")
    except Exception as e:  # noqa: BLE001 - note is best-effort
        logger.warning("get_media_content: could not build download URL for msg %s: %s", msg_id, e)
    suffix = f" — download: {url}" if url else " — no download URL (server not in HTTP public mode)"
    return f"msg {msg_id}: file `{filename}` ({mime}){suffix}"


async def get_media_content_impl(
    chat_id: str, message_ids: list[int]
) -> list[Any]:
    """Return media from specific messages, inlining images as native image content.

    Args:
        chat_id: Target chat (username '@channel', numeric id, or '-100...' form).
        message_ids: Message IDs to fetch media from.

    Returns:
        A list of content blocks (strings and `Image` objects) in message order.
        FastMCP converts each item into a native MCP content block.
    """
    if not message_ids or not isinstance(message_ids, list):
        return ["get_media_content error: message_ids must be a non-empty list of integers"]

    client = await get_connected_client()
    entity = await get_entity_by_id(chat_id)
    if not entity:
        return [f"get_media_content error: cannot find any entity corresponding to '{chat_id}'"]

    messages = await client.get_messages(entity, ids=message_ids)
    if not isinstance(messages, list):
        messages = [messages]

    by_id: dict[int, Any] = {
        getattr(m, "id", None): m for m in messages if m is not None
    }

    # Enforce the per-call image cap up-front, before downloading anything.
    image_ids = [
        mid for mid in message_ids
        if by_id.get(mid) is not None and _classify_media(by_id[mid]) == "image"
    ]
    if len(image_ids) > MAX_IMAGES:
        return [
            f"get_media_content error: {len(image_ids)} image messages requested, "
            f"but the limit is {MAX_IMAGES} per call. Narrow message_ids and retry."
        ]

    blocks: list[Any] = []
    for mid in message_ids:
        msg = by_id.get(mid)
        if msg is None:
            blocks.append(f"msg {mid}: not found or inaccessible")
            continue

        kind = _classify_media(msg)
        if kind == "none":
            blocks.append(f"msg {mid}: no media on this message")
            continue
        if kind in ("voice", "round_video"):
            label = "voice message" if kind == "voice" else "round video"
            blocks.append(
                f"msg {mid}: {label} — not an image; use get_messages "
                "(field `transcription`) for its text."
            )
            continue
        if kind == "other":
            blocks.append(await _file_note(client, msg, entity, mid))
            continue

        if kind == "pdf":
            size = _approx_size_bytes(msg)
            if size is not None and size > MAX_PDF_BYTES:
                mb = size / (1024 * 1024)
                blocks.append(
                    f"msg {mid}: PDF is {mb:.1f} MB (> 20 MB), inline skipped — "
                    + await _file_note(client, msg, entity, mid)
                )
                continue
            try:
                raw = await client.download_media(msg, file=bytes)
            except Exception as e:  # noqa: BLE001
                logger.warning("get_media_content: PDF download failed for msg %s: %s", mid, e)
                blocks.append(f"msg {mid}: failed to download PDF ({e})")
                continue
            if not raw:
                blocks.append(f"msg {mid}: PDF has no downloadable bytes")
                continue
            filename = (_build_media_placeholder(msg) or {}).get("filename") or f"doc_{mid}.pdf"
            blocks.append(f"msg {mid}: {filename} (PDF)")
            blocks.append(File(data=raw, format="pdf", name=filename))
            continue

        # kind == "image"
        size = _approx_size_bytes(msg)
        if size is not None and size > MAX_ORIGINAL_BYTES:
            mb = size / (1024 * 1024)
            blocks.append(
                f"msg {mid}: image is {mb:.1f} MB (> 5 MB), inline skipped — "
                "fetch it by attachment_download_url from get_messages instead."
            )
            continue

        try:
            raw = await client.download_media(msg, file=bytes)
        except Exception as e:  # noqa: BLE001
            logger.warning("get_media_content: download failed for msg %s: %s", mid, e)
            blocks.append(f"msg {mid}: failed to download image ({e})")
            continue
        if not raw:
            blocks.append(f"msg {mid}: image has no downloadable bytes")
            continue

        try:
            jpeg = _downscale_to_jpeg(raw)
        except Exception as e:  # noqa: BLE001
            logger.warning("get_media_content: downscale failed for msg %s: %s", mid, e)
            blocks.append(f"msg {mid}: failed to process image ({e})")
            continue

        blocks.append(f"msg {mid} (chat {chat_id}):")
        blocks.append(Image(data=jpeg, format="jpeg"))

    return blocks
