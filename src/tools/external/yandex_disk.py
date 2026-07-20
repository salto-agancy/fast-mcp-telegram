"""Inline images from a public Yandex.Disk link referenced in Telegram.

Clients often send a Yandex.Disk folder link (e.g. mockups for approval) instead
of attaching the images to the chat. The Telegram connector only sees message
attachments, and web MCP hosts cannot open the Disk URL themselves. This tool
opens the public link server-side (Yandex.Disk public REST API, no auth),
recurses into subfolders, and returns the images INLINE as native MCP image
content — mirroring `get_media_content`.

Non-image files (pdf/docx/...) return a short text note plus a preview/download
URL, since we have no server-side renderer for them.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

from urllib.parse import parse_qsl

import httpx
from fastmcp.utilities.types import Image

from src.tools.messages.media_content import _downscale_to_jpeg

logger = logging.getLogger(__name__)

API = "https://cloud-api.yandex.net/v1/disk/public/resources"
PREVIEW_SIZE = "XXXL"  # Yandex named size (~1280px longest side); big enough to read
LISTING_LIMIT = 200
HTTP_TIMEOUT = 20.0

MAX_IMAGES = 8  # recursive set is larger than a single message; hard ceiling
MAX_DIRS = 60  # anti-runaway cap on folders visited per call

# Hosts we accept as a public Yandex.Disk link (anti-SSRF: the actual HTTP calls
# always go to the fixed API host below, never to an arbitrary caller URL).
_ALLOWED_HOST_SUFFIXES = (
    "disk.yandex.ru",
    "disk.yandex.com",
    "disk.yandex.net",
    "yadi.sk",
    "disk.360.yandex.ru",
    "disk.360.yandex.com",
)


def _is_allowed_yadisk_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return any(host == s or host.endswith("." + s) for s in _ALLOWED_HOST_SUFFIXES)


async def _list_dir(client: httpx.AsyncClient, public_url: str, path: str | None) -> dict:
    """Fetch one folder listing from the Yandex.Disk public API."""
    params: dict[str, Any] = {"public_key": public_url, "limit": LISTING_LIMIT}
    if path:
        params["path"] = path
    resp = await client.get(API, params=params)
    resp.raise_for_status()
    return resp.json()


def _preview_url(item: dict) -> str | None:
    """Preview URL for an item, upsized to a readable size.

    The `preview` link in a listing already carries a small `size=S` param;
    appending another `size=` would be ignored, so we replace the existing one.
    """
    preview = item.get("preview")
    if not isinstance(preview, str) or not preview:
        return None
    parts = urlparse(preview)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "size"]
    query.append(("size", PREVIEW_SIZE))
    return urlunparse(parts._replace(query=urlencode(query)))


async def get_yandex_disk_content_impl(
    public_url: str, path: str | None = None
) -> list[Any]:
    """Open a public Yandex.Disk link and inline its images.

    Args:
        public_url: Public Yandex.Disk link (disk.yandex / yadi.sk).
        path: Optional inner path to start from (default: the link's root).

    Returns:
        A list of content blocks (strings and `Image` objects). FastMCP converts
        each item into a native MCP content block.
    """
    if not public_url or not isinstance(public_url, str):
        return ["get_yandex_disk_content error: public_url must be a non-empty string"]
    if not _is_allowed_yadisk_url(public_url):
        return [
            "get_yandex_disk_content error: only public Yandex.Disk links "
            "(disk.yandex.ru / yadi.sk) are supported."
        ]

    blocks: list[Any] = []
    images_added = 0
    dirs_visited = 0
    skipped_images = 0  # images we could not add because of the cap

    queue: list[str | None] = [path]
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        while queue:
            current = queue.pop(0)
            dirs_visited += 1
            if dirs_visited > MAX_DIRS:
                blocks.append(
                    f"note: stopped after visiting {MAX_DIRS} folders (safety cap); "
                    "some subfolders were not scanned."
                )
                break
            try:
                data = await _list_dir(client, public_url, current)
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                if current is None:
                    hint = " (link may be private, expired, or wrong)" if code in (403, 404) else ""
                    return [f"get_yandex_disk_content error: Yandex.Disk API returned {code}{hint}"]
                blocks.append(f"note: could not open subfolder '{current}' (HTTP {code})")
                continue
            except Exception as e:  # noqa: BLE001
                if current is None:
                    return [f"get_yandex_disk_content error: {e}"]
                blocks.append(f"note: could not open subfolder '{current}' ({e})")
                continue

            # A file link (not a folder) resolves directly to a resource dict.
            embedded = data.get("_embedded")
            if not isinstance(embedded, dict):
                items = [data] if data.get("type") == "file" else []
            else:
                items = embedded.get("items") or []

            for item in items:
                if not isinstance(item, dict):
                    continue
                itype = item.get("type")
                name = item.get("name") or "(unnamed)"
                item_path = item.get("path")

                if itype == "dir":
                    if item_path:
                        queue.append(item_path)
                    continue

                mime = item.get("mime_type") or ""
                is_image = isinstance(mime, str) and mime.startswith("image/")
                if not is_image:
                    size = item.get("size")
                    size_str = f", {size} bytes" if isinstance(size, int) else ""
                    ref = _preview_url(item) or item.get("file") or "(no direct URL)"
                    blocks.append(f"file `{name}` ({mime or 'unknown'}{size_str}) → {ref}")
                    continue

                # image
                if images_added >= MAX_IMAGES:
                    skipped_images += 1
                    continue

                # Prefer Yandex's downscaled preview over the original: the server
                # process has a tight memory limit, and a full-res original would
                # decode into a large in-memory bitmap. A ~1280px preview is enough
                # to read a mockup and keeps peak memory small. Fall back to the
                # original only when no preview is offered.
                src_url = _preview_url(item) or item.get("file")
                if not src_url:
                    blocks.append(f"image `{name}`: no downloadable URL from Yandex.Disk")
                    continue

                try:
                    r = await client.get(src_url)
                    r.raise_for_status()
                    raw = r.content
                except Exception as e:  # noqa: BLE001
                    logger.warning("yadisk: download failed for %s: %s", name, e)
                    blocks.append(f"image `{name}`: failed to download ({e})")
                    continue

                try:
                    jpeg = _downscale_to_jpeg(raw)
                except Exception as e:  # noqa: BLE001
                    logger.warning("yadisk: downscale failed for %s: %s", name, e)
                    blocks.append(f"image `{name}`: failed to process ({e})")
                    continue

                label = item_path or name
                blocks.append(f"{label}:")
                blocks.append(Image(data=jpeg, format="jpeg"))
                images_added += 1

    if skipped_images:
        blocks.append(
            f"note: showed the first {images_added} images; {skipped_images} more were "
            f"not inlined (max {MAX_IMAGES} per call). Narrow with `path` to see the rest."
        )
    if not blocks:
        blocks.append("Yandex.Disk link opened, but no files were found.")
    return blocks
