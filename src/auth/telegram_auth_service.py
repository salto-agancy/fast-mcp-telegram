"""Telegram authentication service for OIDC elicitation.

Wraps Telethon's send_code_request / sign_in flow behind a stateless
service interface that the elicitation tools call.  All mutable state
lives in the DB (setup_state table); this module is pure orchestration.

Design constraints (from ADR 0002 / design brief):
- stdlib sqlite3 only, no SQLAlchemy
- Per-user .session files preserved (Option B) for Telethon cache
- Concurrent sign-in protection via lockfile + single-flight
- 5-min TTL enforced by caller (state machine), not here
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path
from typing import NamedTuple

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)

logger = logging.getLogger(__name__)

# Lockfile directory — created lazily
_LOCK_DIR = Path(os.environ.get("TG_OIDC_LOCK_DIR", "/tmp/tg_oidc_locks"))


class SendCodeResult(NamedTuple):
    """Result of sending a verification code to a phone number."""

    phone_code_hash: str
    next_state: str  # WAITING_CODE


class SignInResult(NamedTuple):
    """Result of a sign-in attempt (code or password step)."""

    success: bool
    next_state: str  # WAITING_PASS, COMPLETED, or FAILED
    session_string: str | None = None  # Only on COMPLETED
    user_id: int | None = None  # Telegram user ID (on COMPLETED)
    username: str | None = None  # Telegram username without @ (on COMPLETED)
    error: str | None = None  # Human-readable error on failure


def _lock_path(oidc_key: str) -> Path:
    """Return the lockfile path for a given oidc_key."""
    safe = hashlib.sha256(oidc_key.encode()).hexdigest()[:16]
    return _LOCK_DIR / f"{safe}.setup.lock"


class TelegramAuthService:
    """Stateless wrapper around Telethon for elicitation sign-in.

    Each method is idempotent w.r.t. DB state — the caller (elicitation
    tools) owns state transitions.  This service only talks to Telegram
    and returns results.

    Concurrency: uses per-oidc_key lockfiles to prevent two simultaneous
    sign-in attempts for the same OIDC identity from racing on Telethon.
    """

    def __init__(
        self,
        api_id: int | None = None,
        api_hash: str | None = None,
        session_dir: str | None = None,
    ) -> None:
        self._api_id = api_id or int(os.environ["TG_API_ID"])
        self._api_hash = api_hash or os.environ["TG_API_HASH"]
        self._session_dir = Path(
            session_dir or os.environ.get("TG_SESSION_DIR", ".sessions")
        )
        self._session_dir.mkdir(parents=True, exist_ok=True)
        _LOCK_DIR.mkdir(parents=True, exist_ok=True)

    def _client(self, oidc_key: str) -> TelegramClient:
        """Create a TelegramClient with a per-OIDC-key session file.

        The session file name is derived from oidc_key so each OIDC
        identity gets its own Telethon cache (entities, auth_key, etc.).
        """
        safe_name = hashlib.sha256(oidc_key.encode()).hexdigest()[:16]
        session_path = str(self._session_dir / f"oidc_{safe_name}")
        return TelegramClient(session_path, self._api_id, self._api_hash)

    async def send_code(
        self, oidc_key: str, phone_number: str
    ) -> SendCodeResult:
        """Send a verification code to *phone_number*.

        Returns the phone_code_hash needed for the subsequent sign_in call.
        Raises on FloodWait (caller should surface retry-after to user).
        """
        lock = _lock_path(oidc_key)
        if lock.exists():
            raise RuntimeError(
                f"Concurrent sign-in already in progress for {oidc_key[:8]}…"
            )

        try:
            lock.touch()
            client = self._client(oidc_key)
            await client.connect()
            try:
                result = await client.send_code_request(phone_number)
                return SendCodeResult(
                    phone_code_hash=result.phone_code_hash,
                    next_state="WAITING_CODE",
                )
            finally:
                # Disconnect but DON'T destroy the session — we need it
                # for the subsequent sign_in call.
                await client.disconnect()
        except FloodWaitError as e:
            raise RuntimeError(
                f"Telegram rate limit: retry after {e.seconds}s"
            ) from e
        finally:
            # Release lock after code send; re-acquired on sign_in
            lock.unlink(missing_ok=True)

    async def verify_code(
        self,
        oidc_key: str,
        phone_number: str,
        phone_code_hash: str,
        code: str,
    ) -> SignInResult:
        """Verify a code.  Returns WAITING_PASS if 2FA required."""
        lock = _lock_path(oidc_key)
        if lock.exists():
            return SignInResult(
                success=False, next_state="FAILED",
                error="Concurrent sign-in in progress",
            )

        try:
            lock.touch()
            client = self._client(oidc_key)
            await client.connect()
            try:
                me = await client.sign_in(
                    phone=phone_number,
                    code=code,
                    phone_code_hash=phone_code_hash,
                )
                session_str = await client.export_session_string()
                return SignInResult(
                    success=True,
                    next_state="COMPLETED",
                    session_string=session_str,
                    user_id=me.id if me else None,
                    username=me.username if me else None,
                )
            except SessionPasswordNeededError:
                return SignInResult(
                    success=True,
                    next_state="WAITING_PASS",
                )
            except PhoneCodeInvalidError:
                return SignInResult(
                    success=False,
                    next_state="WAITING_CODE",
                    error="Invalid code",
                )
            except PhoneCodeExpiredError:
                return SignInResult(
                    success=False,
                    next_state="FAILED",
                    error="Code expired — restart setup",
                )
            finally:
                await client.disconnect()
        finally:
            lock.unlink(missing_ok=True)

    async def verify_password(
        self, oidc_key: str, password: str
    ) -> SignInResult:
        """Complete 2FA sign-in with password."""
        lock = _lock_path(oidc_key)
        if lock.exists():
            return SignInResult(
                success=False, next_state="FAILED",
                error="Concurrent sign-in in progress",
            )

        try:
            lock.touch()
            client = self._client(oidc_key)
            await client.connect()
            try:
                me = await client.sign_in(password=password)
                session_str = await client.export_session_string()
                return SignInResult(
                    success=True,
                    next_state="COMPLETED",
                    session_string=session_str,
                    user_id=me.id if me else None,
                    username=me.username if me else None,
                )
            except PasswordHashInvalidError:
                return SignInResult(
                    success=False,
                    next_state="WAITING_PASS",
                    error="Invalid password",
                )
            finally:
                await client.disconnect()
        finally:
            lock.unlink(missing_ok=True)
