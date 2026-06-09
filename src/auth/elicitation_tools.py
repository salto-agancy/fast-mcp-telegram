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

import json
import logging
import os
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
from .principal_resolver import resolve_principal
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


async def oidc_setup_start(oidc_key: str, db_path: Optional[str] = None) -> dict:
    """Initialize or resume an OIDC Telegram elicitation session.

    Args:
        oidc_key: Hashed OIDC identity key (sub:issuer).
        db_path: Optional DB path override (for testing).

    Returns:
        Dict with success, state, message, needs_2fa fields.
    """
    # Check if already mapped — query DB directly since we have oidc_key
    existing_identity = id_queries.get_identity(oidc_key, db_path=db_path)
    if existing_identity is not None:
        # Build principal string from identity row
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
    return _result_to_dict(result)


async def oidc_setup_phone(
    oidc_key: str, phone: str, db_path: Optional[str] = None
) -> dict:
    """Submit phone number for Telegram verification.

    Args:
        oidc_key: Hashed OIDC identity key.
        phone: Telegram phone number (e.g. +1234567890).
        db_path: Optional DB path override.

    Returns:
        Dict with success, state, message fields.
    """
    result = submit_phone(oidc_key, phone, db_path=db_path)
    if not result.success:
        return _result_to_dict(result)

    # Send verification code via Telethon
    try:
        service = _get_auth_service()
        code_result = await service.send_code(oidc_key, phone)

        # Store phone_code_hash in metadata for later verification
        existing_row = None
        with db.get_connection(db_path) as conn:
            row = conn.execute(
                "SELECT metadata FROM setup_state WHERE oidc_key = ?",
                (oidc_key,),
            ).fetchone()
            if row:
                existing_row = dict(row)

        meta = {}
        if existing_row and existing_row.get("metadata"):
            try:
                meta = json.loads(existing_row["metadata"]) if isinstance(
                    existing_row["metadata"], str
                ) else existing_row["metadata"]
            except (json.JSONDecodeError, TypeError):
                pass
        meta["phone_code_hash"] = code_result.phone_code_hash
        meta["phone_number"] = phone

        ss_queries.transition_state(
            oidc_key,
            ElicitState.WAITING_CODE.value,
            metadata=json.dumps(meta),
            db_path=db_path,
        )
        return _result_to_dict(result)

    except RuntimeError as e:
        logger.error("Failed to send code for %s: %s", oidc_key[:8], e)
        retry_result = record_retry(oidc_key, db_path=db_path)
        return _result_to_dict(retry_result)


async def oidc_setup_code(
    oidc_key: str, code: str, db_path: Optional[str] = None
) -> dict:
    """Submit Telegram verification code.

    Args:
        oidc_key: Hashed OIDC identity key.
        code: Verification code from Telegram.
        db_path: Optional DB path override.

    Returns:
        Dict with success, state, message, needs_2fa fields.
    """
    # Fetch current state
    existing = None
    with db.get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT oidc_key, state, phone_number, tg_code_hash, retry_count, metadata, created_at, updated_at "
            "FROM setup_state WHERE oidc_key = ?",
            (oidc_key,),
        ).fetchone()
        if row:
            existing = dict(row)

    if existing is None:
        return _result_to_dict(
            ElicitResult(False, ElicitState.FAILED, "No active session.")
        )

    # Extract phone_number and phone_code_hash from metadata
    meta = {}
    if existing.get("metadata"):
        try:
            meta = json.loads(existing["metadata"]) if isinstance(
                existing["metadata"], str
            ) else existing["metadata"]
        except (json.JSONDecodeError, TypeError):
            pass

    phone_number = meta.get("phone_number") or existing.get("phone_number")
    phone_code_hash = meta.get("phone_code_hash")

    if not phone_number or not phone_code_hash:
        return _result_to_dict(
            ElicitResult(
                False, ElicitState.FAILED,
                "Missing phone or code hash. Restart setup.",
            )
        )

    # Verify code via Telethon
    try:
        service = _get_auth_service()
        sign_in_result = await service.verify_code(
            oidc_key, phone_number, phone_code_hash, code
        )

        if sign_in_result.success:
            if sign_in_result.next_state == "COMPLETED":
                # Save identity mapping
                oidc_sub = oidc_key.split(":")[0] if ":" in oidc_key else oidc_key
                oidc_issuer = oidc_key.split(":")[1] if ":" in oidc_key else "unknown"
                id_queries.insert_identity(
                    oidc_key=oidc_key,
                    oidc_sub=oidc_sub,
                    oidc_issuer=oidc_issuer,
                    telegram_user_id=sign_in_result.user_id or 0,
                    telegram_username=sign_in_result.username,
                    telegram_phone=phone_number,
                    db_path=db_path,
                )
                # Save session file to disk and record metadata in DB
                if sign_in_result.session_string:
                    import hashlib
                    safe_name = hashlib.sha256(oidc_key.encode()).hexdigest()[:16]
                    session_dir = os.environ.get("TG_SESSION_DIR", ".sessions")
                    from pathlib import Path
                    Path(session_dir).mkdir(parents=True, exist_ok=True)
                    session_file = Path(session_dir) / f"oidc_{safe_name}.session"
                    session_file.write_text(sign_in_result.session_string)
                    # Insert session metadata (auth_key placeholder for now;
                    # real auth_key extracted on next Telethon connect)
                    from src.auth.queries.telegram_session import insert_session
                    try:
                        insert_session(
                            oidc_key=oidc_key,
                            session_filename=str(session_file),
                            dc_id=0,
                            server_address="",
                            port=0,
                            auth_key=b"",
                            db_path=db_path,
                        )
                    except Exception:
                        pass  # FK may fail if identity insert failed; non-critical
                ss_queries.transition_state(
                    oidc_key, ElicitState.COMPLETED.value, db_path=db_path
                )
                return _result_to_dict(
                    ElicitResult(
                        True, ElicitState.COMPLETED,
                        "Telegram account linked successfully.",
                    )
                )
            elif sign_in_result.next_state == "WAITING_PASS":
                # Mark that 2FA is needed
                meta["needs_2fa"] = True
                ss_queries.transition_state(
                    oidc_key,
                    ElicitState.WAITING_PASS.value,
                    metadata=json.dumps(meta),
                    db_path=db_path,
                )
                return _result_to_dict(
                    ElicitResult(
                        True, ElicitState.WAITING_PASS,
                        "2FA enabled. Enter your Telegram password.",
                        needs_2fa=True,
                    )
                )

        # Code was invalid or expired
        retry_result = record_retry(oidc_key, db_path=db_path)
        return _result_to_dict(retry_result)

    except RuntimeError as e:
        logger.error("Code verification failed for %s: %s", oidc_key[:8], e)
        retry_result = record_retry(oidc_key, db_path=db_path)
        return _result_to_dict(retry_result)


async def oidc_setup_password(
    oidc_key: str, password: str, db_path: Optional[str] = None
) -> dict:
    """Submit Telegram 2FA password.

    Args:
        oidc_key: Hashed OIDC identity key.
        password: Telegram 2FA password.
        db_path: Optional DB path override.

    Returns:
        Dict with success, state, message fields.
    """
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

            oidc_sub = oidc_key.split(":")[0] if ":" in oidc_key else oidc_key
            oidc_issuer = oidc_key.split(":")[1] if ":" in oidc_key else "unknown"
            id_queries.insert_identity(
                oidc_key=oidc_key,
                oidc_sub=oidc_sub,
                oidc_issuer=oidc_issuer,
                telegram_user_id=sign_in_result.user_id or 0,
                telegram_username=sign_in_result.username,
                telegram_phone=phone_number,
                db_path=db_path,
            )
            # Save session file to disk and record metadata in DB
            if sign_in_result.session_string:
                import hashlib
                safe_name = hashlib.sha256(oidc_key.encode()).hexdigest()[:16]
                session_dir = os.environ.get("TG_SESSION_DIR", ".sessions")
                from pathlib import Path
                Path(session_dir).mkdir(parents=True, exist_ok=True)
                session_file = Path(session_dir) / f"oidc_{safe_name}.session"
                session_file.write_text(sign_in_result.session_string)
                from src.auth.queries.telegram_session import insert_session
                try:
                    insert_session(
                        oidc_key=oidc_key,
                        session_filename=str(session_file),
                        dc_id=0,
                        server_address="",
                        port=0,
                        auth_key=b"",
                        db_path=db_path,
                    )
                except Exception:
                    pass
            ss_queries.transition_state(
                oidc_key, ElicitState.COMPLETED.value, db_path=db_path
            )
            return _result_to_dict(
                ElicitResult(
                    True, ElicitState.COMPLETED,
                    "Telegram account linked successfully.",
                )
            )

        # Password was invalid
        retry_result = record_retry(oidc_key, db_path=db_path)
        return _result_to_dict(retry_result)

    except RuntimeError as e:
        logger.error("Password verification failed for %s: %s", oidc_key[:8], e)
        retry_result = record_retry(oidc_key, db_path=db_path)
        return _result_to_dict(retry_result)
