"""FastMCP tools for OIDC Telegram elicitation.

These tools are called by OIDC-authenticated users who lack a Telegram
identity mapping.  The user is already authenticated via OIDC JWT when
calling these tools — they handle the multi-round Telegram sign-in flow.

Tools:
    oidc_setup_start   — Initialize or resume elicitation session
    oidc_setup_phone   — Submit phone number
    oidc_setup_code    — Submit verification code
    oidc_setup_password — Submit 2FA password
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Optional

from .queries import oidc_identity as id_queries
from .queries import setup_state as ss_queries
from . import db
from .elicitation_state_machine import (
    ElicitResult,
    ElicitState,
    record_retry,
    start_elicitation,
    submit_code,
    submit_password,
    submit_phone,
)
from .telegram_auth_service import TelegramAuthService

logger = logging.getLogger(__name__)

# Module-level service instance (lazy init)
_auth_service: Optional[TelegramAuthService] = None


def _get_auth_service() -> TelegramAuthService:
    global _auth_service
    if _auth_service is None:
        _auth_service = TelegramAuthService()
    return _auth_service


def _result_to_dict(result: ElicitResult) -> dict:
    """Convert ElicitResult to JSON-serializable dict."""
    return {
        "success": result.success,
        "state": result.new_state.value,
        "message": result.message,
        "needs_2fa": result.needs_2fa,
    }


def _save_session_file(oidc_key: str, session_string: str) -> str:
    """Write Telethon session string to disk and return the file path."""
    safe_name = hashlib.sha256(oidc_key.encode()).hexdigest()[:16]
    session_dir = os.environ.get("TG_SESSION_DIR", ".sessions")
    Path(session_dir).mkdir(parents=True, exist_ok=True)
    session_file = Path(session_dir) / f"oidc_{safe_name}.session"
    session_file.write_text(session_string)
    return str(session_file)


def _record_session_metadata(
    oidc_key: str, session_filename: str, db_path: Optional[str] = None
) -> None:
    """Insert telegram_session metadata row. Non-critical — logs on failure."""
    try:
        from .queries.telegram_session import insert_session
        insert_session(
            oidc_key=oidc_key,
            session_filename=session_filename,
            dc_id=0,
            server_address="",
            port=0,
            auth_key=b"",
            db_path=db_path,
        )
    except Exception:
        logger.debug("Session metadata insert failed for %s (non-critical)", oidc_key[:8])


def _save_identity_and_session(
    oidc_key: str,
    oidc_sub: str,
    oidc_issuer: str,
    sign_in_result,
    phone_number: str,
    db_path: Optional[str] = None,
) -> None:
    """Persist OIDC identity mapping and Telethon session after successful sign-in.

    Args:
        oidc_key: Pre-hashed identity key (sha256 of sub:issuer).
        oidc_sub: Raw OIDC subject claim from JWT.
        oidc_issuer: Raw OIDC issuer URL from JWT.
        sign_in_result: SignInResult with user_id, username, session_string.
        phone_number: Telegram phone number used for verification.
        db_path: Optional DB path override.
    """
    id_queries.insert_identity(
        oidc_key=oidc_key,
        oidc_sub=oidc_sub,
        oidc_issuer=oidc_issuer,
        telegram_user_id=sign_in_result.user_id or 0,
        telegram_username=sign_in_result.username,
        telegram_phone=phone_number,
        db_path=db_path,
    )
    if sign_in_result.session_string:
        session_file = _save_session_file(oidc_key, sign_in_result.session_string)
        _record_session_metadata(oidc_key, session_file, db_path=db_path)


async def oidc_setup_start(
    oidc_sub: str, oidc_issuer: str, db_path: Optional[str] = None
) -> dict:
    """Initialize or resume an OIDC Telegram elicitation session.

    Args:
        oidc_sub: Raw OIDC subject claim from JWT.
        oidc_issuer: Raw OIDC issuer URL from JWT.
        db_path: Optional DB path override (for testing).

    Returns:
        Dict with success, state, message, needs_2fa fields.
    """
    oidc_key = id_queries.make_oidc_key(oidc_sub, oidc_issuer)
    existing_identity = id_queries.get_identity(oidc_key, db_path=db_path)
    if existing_identity is not None:
        if existing_identity["telegram_username"]:
            principal = f"@{existing_identity['telegram_username']}"
        elif existing_identity["telegram_phone"]:
            principal = f"+{existing_identity['telegram_phone']}"
        else:
            principal = str(existing_identity["telegram_user_id"])
        return {
            "success": True,
            "state": ElicitState.COMPLETED.value,
            "message": f"Already linked to Telegram principal: {principal}",
            "needs_2fa": False,
        }

    result = start_elicitation(oidc_key, db_path=db_path)
    # Store sub/issuer in metadata for later retrieval
    if result.success and result.new_state == ElicitState.WAITING_PHONE:
        meta = {"oidc_sub": oidc_sub, "oidc_issuer": oidc_issuer}
        ss_queries.transition_state(
            oidc_key,
            ElicitState.WAITING_PHONE.value,
            metadata=json.dumps(meta),
            db_path=db_path,
        )
    return _result_to_dict(result)


async def oidc_setup_phone(
    oidc_sub: str, oidc_issuer: str, phone: str, db_path: Optional[str] = None
) -> dict:
    """Submit phone number for Telegram verification.

    Args:
        oidc_sub: Raw OIDC subject claim.
        oidc_issuer: Raw OIDC issuer URL.
        phone: Telegram phone number (e.g. +1234567890).
        db_path: Optional DB path override.

    Returns:
        Dict with success, state, message fields.
    """
    oidc_key = id_queries.make_oidc_key(oidc_sub, oidc_issuer)
    result = submit_phone(oidc_key, phone, db_path=db_path)
    if not result.success:
        return _result_to_dict(result)

    try:
        service = _get_auth_service()
        code_result = await service.send_code(oidc_key, phone)

        # Store phone_code_hash in metadata for later verification
        meta = {"phone_code_hash": code_result.phone_code_hash, "phone_number": phone}
        ss_queries.transition_state(
            oidc_key,
            ElicitState.WAITING_CODE.value,
            metadata=json.dumps(meta),
            db_path=db_path,
        )
        return _result_to_dict(result)

    except RuntimeError as e:
        error_msg = str(e)
        logger.error("Failed to send code for %s: %s", oidc_key[:8], error_msg)
        # Don't record retry for concurrency conflicts — not a user error
        if "Concurrent sign-in" in error_msg:
            return _result_to_dict(
                ElicitResult(False, ElicitState.WAITING_PHONE, error_msg)
            )
        retry_result = record_retry(oidc_key, db_path=db_path)
        return _result_to_dict(retry_result)


async def oidc_setup_code(
    oidc_sub: str, oidc_issuer: str, code: str, db_path: Optional[str] = None
) -> dict:
    """Submit Telegram verification code.

    Args:
        oidc_sub: Raw OIDC subject claim.
        oidc_issuer: Raw OIDC issuer URL.
        code: Verification code from Telegram.
        db_path: Optional DB path override.

    Returns:
        Dict with success, state, message, needs_2fa fields.
    """
    oidc_key = id_queries.make_oidc_key(oidc_sub, oidc_issuer)
    # Fetch metadata for phone_number and phone_code_hash
    with db.get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT metadata FROM setup_state WHERE oidc_key = ?",
            (oidc_key,),
        ).fetchone()

    if row is None:
        return _result_to_dict(
            ElicitResult(False, ElicitState.FAILED, "No active session.")
        )

    meta = {}
    if row["metadata"]:
        try:
            meta = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
        except (json.JSONDecodeError, TypeError):
            pass

    phone_number = meta.get("phone_number")
    phone_code_hash = meta.get("phone_code_hash")

    if not phone_number or not phone_code_hash:
        return _result_to_dict(
            ElicitResult(False, ElicitState.FAILED, "Missing phone or code hash. Restart setup.")
        )

    try:
        service = _get_auth_service()
        sign_in_result = await service.verify_code(
            oidc_key, phone_number, phone_code_hash, code
        )

        if sign_in_result.success:
            if sign_in_result.next_state == "COMPLETED":
                _save_identity_and_session(
                    oidc_key, oidc_sub, oidc_issuer, sign_in_result, phone_number, db_path
                )
                transition = submit_code(oidc_key, needs_2fa=False, db_path=db_path)
                return _result_to_dict(transition)
            elif sign_in_result.next_state == "WAITING_PASS":
                transition = submit_code(oidc_key, needs_2fa=True, db_path=db_path)
                return _result_to_dict(transition)

        # Code was invalid or expired — record retry
        retry_result = record_retry(oidc_key, db_path=db_path)
        return _result_to_dict(retry_result)

    except RuntimeError as e:
        error_msg = str(e)
        logger.error("Code verification failed for %s: %s", oidc_key[:8], error_msg)
        if "Concurrent sign-in" in error_msg:
            return _result_to_dict(
                ElicitResult(False, ElicitState.WAITING_CODE, error_msg)
            )
        retry_result = record_retry(oidc_key, db_path=db_path)
        return _result_to_dict(retry_result)


async def oidc_setup_password(
    oidc_sub: str, oidc_issuer: str, password: str, db_path: Optional[str] = None
) -> dict:
    """Submit Telegram 2FA password.

    Args:
        oidc_sub: Raw OIDC subject claim.
        oidc_issuer: Raw OIDC issuer URL.
        password: Telegram 2FA password.
        db_path: Optional DB path override.

    Returns:
        Dict with success, state, message fields.
    """
    oidc_key = id_queries.make_oidc_key(oidc_sub, oidc_issuer)
    try:
        service = _get_auth_service()
        sign_in_result = await service.verify_password(oidc_key, password)

        if sign_in_result.success and sign_in_result.next_state == "COMPLETED":
            # Fetch phone from state for identity record
            phone_number = None
            with db.get_connection(db_path) as conn:
                row = conn.execute(
                    "SELECT phone_number FROM setup_state WHERE oidc_key = ?",
                    (oidc_key,),
                ).fetchone()
                if row:
                    phone_number = row["phone_number"]

            _save_identity_and_session(
                oidc_key, oidc_sub, oidc_issuer, sign_in_result, phone_number or "", db_path
            )
            transition = submit_password(oidc_key, db_path=db_path)
            return _result_to_dict(transition)

        # Password was invalid
        retry_result = record_retry(oidc_key, db_path=db_path)
        return _result_to_dict(retry_result)

    except RuntimeError as e:
        error_msg = str(e)
        logger.error("Password verification failed for %s: %s", oidc_key[:8], error_msg)
        if "Concurrent sign-in" in error_msg:
            return _result_to_dict(
                ElicitResult(False, ElicitState.WAITING_PASS, error_msg)
            )
        retry_result = record_retry(oidc_key, db_path=db_path)
        return _result_to_dict(retry_result)
