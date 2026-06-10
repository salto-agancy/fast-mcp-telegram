"""
Anonymous feature-adoption telemetry for fast-mcp-telegram.

Sends periodic heartbeats with anonymised feature-flag and counter data
to the maintainer's collection endpoint.  Opt-out via ``DO_NOT_TRACK=1``.

See ADR 0005 for full design.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import platform
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from src.config.server_config import cfg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TELEMETRY_ENDPOINT = "https://mcp-telemetry.l1979.ru/v1/event"
"""Stable URL baked into each release.  The destination may 301-forward."""

_DEFAULT_CONFIG_DIR = Path.home() / ".config" / "fast-mcp-telegram"
"""Directory where the instance-id file lives."""

_MIN_INTERVAL_SECONDS = 3600
"""Minimum heartbeat interval (1 hour)."""

_DEFAULT_INTERVAL_SECONDS = 6 * 3600
"""Default interval between heartbeats (6 hours)."""

# ---------------------------------------------------------------------------
# Opt-out
# ---------------------------------------------------------------------------


def should_send() -> bool:
    """Return ``True`` when telemetry is allowed (the default)."""
    do_not_track = os.environ.get("DO_NOT_TRACK", "").strip()
    return do_not_track != "1"


# ---------------------------------------------------------------------------
# MetricsStore — shared between tool-call sites and the telemetry task
# ---------------------------------------------------------------------------


@dataclass
class MetricsStore:
    """Lifetime-of-process counters for tool-call activity."""

    total_calls: int = 0
    errors: int = 0
    flood_waits: int = 0

    def snapshot(self) -> dict:
        """Return a frozen copy of the current counters as a plain dict."""
        return {
            "total_calls": self.total_calls,
            "errors": self.errors,
            "flood_waits": self.flood_waits,
        }


# Module-level singleton — wired by server.py during lifespan.
metrics = MetricsStore()

# ---------------------------------------------------------------------------
# Instance ID — persisted across restarts
# ---------------------------------------------------------------------------

_instance_id: str | None = None
"""In-memory cache of the instance ID (avoids re-reading the file every heartbeat)."""


def get_instance_id(config_dir: str | None = None) -> str:
    """Return the persistent instance ID, creating a new UUID v4 if needed.

    The ID is stored in ``{config_dir}/instance_id`` (defaults to
    ``~/.config/fast-mcp-telegram/instance_id``) and survives package reinstall,
    but can be reset by deleting that file.
    """
    global _instance_id

    if _instance_id is not None:
        return _instance_id

    cfg_dir = Path(config_dir) if config_dir else _DEFAULT_CONFIG_DIR
    id_path = cfg_dir / "instance_id"

    if id_path.is_file():
        _instance_id = id_path.read_text(encoding="utf-8").strip()
        return _instance_id

    _instance_id = str(uuid.uuid4())
    cfg_dir.mkdir(parents=True, exist_ok=True)
    id_path.write_text(_instance_id, encoding="utf-8")
    return _instance_id


# ---------------------------------------------------------------------------
# Payload assembly
# ---------------------------------------------------------------------------

# Track server start once per process.
_started_at: int = int(time.time())


def _collect_features() -> dict:
    """Assemble the ``features`` block of the telemetry payload."""
    config = cfg()
    return {
        "server_mode": config.server_mode.value,
        "acl_enabled": config.acl_enabled,
        "acl_deny_unlisted_principals": config.acl_deny_unlisted_principals,
        "bot_api_token": bool(config.bot_api_token),
        "mtproto_proxy": bool(config.mtproto_proxy),
        "prefix_mcp_tools_with_account": config.prefix_mcp_tools_with_account,
        "max_active_sessions": config.max_active_sessions,
        "inactive_session_days": config.inactive_session_days,
        "block_private_ips": config.block_private_ips,
        "allow_http_urls": config.allow_http_urls,
    }


def _collect_runtime() -> dict:
    """Assemble the ``runtime`` block of the telemetry payload."""
    config = cfg()
    session_dir = config.session_directory
    session_files = 0
    if session_dir.is_dir():
        session_files = sum(
            1 for f in session_dir.iterdir() if f.suffix == ".session"
        )

    return {
        "sessions": 0,  # placeholder — wired in server.py when client is active
        "session_files": session_files,
        "setup_sessions": 0,  # placeholder
    }


def gather_payload() -> dict:
    """Return a self-contained dictionary that can be sent as a heartbeat."""
    from src.server_components.session_acl import principal_count, read_only_count

    payload = {
        "v": 1,
        "iid": get_instance_id(),
        "ts": int(time.time()),
        "started_at": _started_at,
        "ver": "0.30.1",
        "os": f"{sys.platform} {platform.machine()}",
        "py": f"{sys.version_info.major}.{sys.version_info.minor}",
        "features": _collect_features(),
        "runtime": _collect_runtime(),
        "counters": metrics.snapshot(),
    }

    # ACL depth is added to the features block, not sourced from config so we
    # call the new methods on SessionACL.
    payload["features"]["acl_principals"] = principal_count()
    payload["features"]["acl_read_only"] = read_only_count()

    return payload


# ---------------------------------------------------------------------------
# Send (synchronous — wrapped in a thread by the async loop)
# ---------------------------------------------------------------------------


def send_heartbeat(payload: dict | None = None) -> None:
    """Transmit a telemetry heartbeat.

    When ``MCP_TELEMETRY_DEBUG=1`` the payload is logged to stderr instead of
    being sent.  Network errors are silently logged at DEBUG level.
    """
    if not should_send():
        return

    if payload is None:
        payload = gather_payload()

    # Debug mode — print to stderr instead of sending
    if os.environ.get("MCP_TELEMETRY_DEBUG", "").strip() == "1":
        print("TELEMETRY", json.dumps(payload, indent=2), file=sys.stderr)
        return

    # Fire the POST (blocking in this thread — caller runs us in a thread)
    import urllib.error
    import urllib.request

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        TELEMETRY_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as exc:
        logger.debug("Telemetry: HTTP %s from %s", exc.code, TELEMETRY_ENDPOINT)
    except (OSError, urllib.error.URLError) as exc:
        logger.debug("Telemetry: network error — %s", exc)


# ---------------------------------------------------------------------------
# Asynchronous telemetry loop (started in server.py lifespan)
# ---------------------------------------------------------------------------


async def telemetry_task() -> None:
    """Background ``asyncio`` task that sends heartbeats periodically."""
    if not should_send():
        logger.info("Telemetry: disabled (DO_NOT_TRACK=1)")
        return

    logger.info("Telemetry: enabled (disable with DO_NOT_TRACK=1)")

    # Send an immediate heartbeat on startup.
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, send_heartbeat)
    except Exception:
        logger.debug("Telemetry: startup heartbeat failed", exc_info=True)

    # Subsequent heartbeats at the configured interval.
    raw_interval = os.environ.get("MCP_TELEMETRY_INTERVAL", "")
    interval = _DEFAULT_INTERVAL_SECONDS
    if raw_interval.strip():
        with contextlib.suppress(ValueError):
            interval = max(int(raw_interval.strip()), _MIN_INTERVAL_SECONDS)

    while True:
        try:
            await asyncio.sleep(interval)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, send_heartbeat)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.debug("Telemetry: periodic heartbeat failed", exc_info=True)


# ---------------------------------------------------------------------------
# Server-config integration
# ---------------------------------------------------------------------------

# ``server_config.py`` reads ``DO_NOT_TRACK`` as an env-var-only check (no
# pydantic field needed).  The check is trivial and lives in ``should_send()``
# above — the config module does not need a dedicated property.
