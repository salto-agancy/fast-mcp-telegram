"""OIDC Elicitation State Machine.

Manages multi-round Telegram sign-in flow for OIDC-authenticated users
who lack a Telegram identity mapping.

States:
    WAITING_PHONE -> WAITING_CODE -> WAITING_PASS -> COMPLETED
                                -> FAILED
    Any state -> EXPIRED (after TTL)

TTL: 5 minutes per state transition.
Retry: 1 retry allowed per state (wrong code/password), then FAILED.
"""

import enum
import json
from datetime import datetime, timezone, timedelta
from typing import Optional

from .queries import setup_state as ss_queries
from .queries import oidc_identity as id_queries
from . import db

TTL_SECONDS = 300  # 5 minutes
MAX_RETRIES = 1


class ElicitState(str, enum.Enum):
    WAITING_PHONE = "WAITING_PHONE"
    WAITING_CODE = "WAITING_CODE"
    WAITING_PASS = "WAITING_PASS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class ElicitResult:
    """Result of a state machine transition."""
    __slots__ = ("success", "new_state", "message", "needs_2fa")

    def __init__(self, success: bool, new_state: ElicitState, message: str, needs_2fa: bool = False):
        self.success = success
        self.new_state = new_state
        self.message = message
        self.needs_2fa = needs_2fa


def _is_expired(updated_at_iso: str) -> bool:
    """Check if an ISO timestamp is older than TTL_SECONDS."""
    try:
        updated = datetime.strptime(updated_at_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - updated) > timedelta(seconds=TTL_SECONDS)
    except (ValueError, TypeError):
        return True


def _get_state_row(oidc_key: str, db_path: Optional[str] = None) -> Optional[dict]:
    """Fetch current state row as dict via get_active_states or direct query."""
    with db.get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT oidc_key, state, phone_number, tg_code_hash, retry_count, metadata, created_at, updated_at "
            "FROM setup_state WHERE oidc_key = ?",
            (oidc_key,),
        ).fetchone()
        return dict(row) if row else None


def start_elicitation(oidc_key: str, db_path: Optional[str] = None) -> ElicitResult:
    """Initialize or resume an elicitation session."""
    existing = _get_state_row(oidc_key, db_path=db_path)

    if existing is None:
        ss_queries.create_state(oidc_key, db_path=db_path)
        return ElicitResult(
            success=True,
            new_state=ElicitState.WAITING_PHONE,
            message="Please provide your Telegram phone number (e.g. +1234567890).",
        )

    state_str = existing["state"]
    if state_str == "EXPIRED":
        return ElicitResult(False, ElicitState.EXPIRED, "Session expired. Please start over.")

    try:
        state = ElicitState(state_str)
    except ValueError:
        return ElicitResult(False, ElicitState.FAILED, f"Unknown state: {state_str}")

    if state in (ElicitState.COMPLETED, ElicitState.FAILED):
        return ElicitResult(
            success=(state == ElicitState.COMPLETED),
            new_state=state,
            message="Setup already completed." if state == ElicitState.COMPLETED else "Setup failed. Start a new session.",
        )

    if _is_expired(existing["updated_at"]):
        ss_queries.transition_state(oidc_key, "EXPIRED", db_path=db_path)
        return ElicitResult(False, ElicitState.EXPIRED, "Session expired. Please start over.")

    messages = {
        ElicitState.WAITING_PHONE: "Please provide your Telegram phone number.",
        ElicitState.WAITING_CODE: "Please provide the verification code sent to your Telegram.",
        ElicitState.WAITING_PASS: "Please provide your Telegram 2FA password.",
    }
    return ElicitResult(
        success=True,
        new_state=state,
        message=messages.get(state, "Unknown state."),
        needs_2fa=(state == ElicitState.WAITING_PASS),
    )


def submit_phone(oidc_key: str, phone: str, db_path: Optional[str] = None) -> ElicitResult:
    """Submit phone number. Transitions WAITING_PHONE -> WAITING_CODE.
    
    Uses atomic UPDATE with TTL check to avoid race with sweep task.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=TTL_SECONDS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.get_connection(db_path) as conn:
        cur = conn.execute(
            "UPDATE setup_state SET state = ?, phone_number = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
            "WHERE oidc_key = ? AND state = ? AND updated_at >= ?",
            (ElicitState.WAITING_CODE.value, phone, oidc_key, ElicitState.WAITING_PHONE.value, cutoff),
        )
        if cur.rowcount == 0:
            # Either no session, wrong state, or expired
            row = _get_state_row(oidc_key, db_path=db_path)
            if row is None:
                return ElicitResult(False, ElicitState.FAILED, "No active session. Call start first.")
            if _is_expired(row["updated_at"]):
                ss_queries.transition_state(oidc_key, "EXPIRED", db_path=db_path)
                return ElicitResult(False, ElicitState.EXPIRED, "Session expired.")
            return ElicitResult(False, ElicitState.FAILED, f"Expected WAITING_PHONE, got {row['state']}.")
    return ElicitResult(
        success=True,
        new_state=ElicitState.WAITING_CODE,
        message=f"Code sent to {phone}. Enter the code from Telegram.",
    )


def submit_code(oidc_key: str, needs_2fa: bool = False, db_path: Optional[str] = None) -> ElicitResult:
    """Transition after code verification. WAITING_CODE -> WAITING_PASS or COMPLETED.
    
    Uses atomic UPDATE with TTL check to avoid race with sweep task.
    The tools layer performs actual Telethon verification; this function
    only handles the state transition after verification succeeds.
    
    Args:
        oidc_key: Hashed OIDC identity key.
        needs_2fa: True if Telethon raised SessionPasswordNeededError.
        db_path: Optional DB path override.
    """
    target_state = ElicitState.WAITING_PASS if needs_2fa else ElicitState.COMPLETED
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=TTL_SECONDS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    with db.get_connection(db_path) as conn:
        cur = conn.execute(
            "UPDATE setup_state SET state = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
            "WHERE oidc_key = ? AND state = ? AND updated_at >= ?",
            (target_state.value, oidc_key, ElicitState.WAITING_CODE.value, cutoff),
        )
        if cur.rowcount == 0:
            row = _get_state_row(oidc_key, db_path=db_path)
            if row is None:
                return ElicitResult(False, ElicitState.FAILED, "No active session.")
            if _is_expired(row["updated_at"]):
                ss_queries.transition_state(oidc_key, "EXPIRED", db_path=db_path)
                return ElicitResult(False, ElicitState.EXPIRED, "Session expired.")
            return ElicitResult(False, ElicitState.FAILED, f"Expected WAITING_CODE, got {row['state']}.")
    
    if needs_2fa:
        return ElicitResult(True, ElicitState.WAITING_PASS, "2FA enabled. Enter your Telegram password.", needs_2fa=True)
    return ElicitResult(True, ElicitState.COMPLETED, "Telegram account linked successfully.")


def submit_password(oidc_key: str, db_path: Optional[str] = None) -> ElicitResult:
    """Transition after password verification. WAITING_PASS -> COMPLETED.
    
    Uses atomic UPDATE with TTL check to avoid race with sweep task.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=TTL_SECONDS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.get_connection(db_path) as conn:
        cur = conn.execute(
            "UPDATE setup_state SET state = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
            "WHERE oidc_key = ? AND state = ? AND updated_at >= ?",
            (ElicitState.COMPLETED.value, oidc_key, ElicitState.WAITING_PASS.value, cutoff),
        )
        if cur.rowcount == 0:
            row = _get_state_row(oidc_key, db_path=db_path)
            if row is None:
                return ElicitResult(False, ElicitState.FAILED, "No active session.")
            if _is_expired(row["updated_at"]):
                ss_queries.transition_state(oidc_key, "EXPIRED", db_path=db_path)
                return ElicitResult(False, ElicitState.EXPIRED, "Session expired.")
            return ElicitResult(False, ElicitState.FAILED, f"Expected WAITING_PASS, got {row['state']}.")
    return ElicitResult(True, ElicitState.COMPLETED, "Telegram account linked successfully.")


def record_retry(oidc_key: str, db_path: Optional[str] = None) -> ElicitResult:
    """Record a failed attempt. After MAX_RETRIES, transitions to FAILED."""
    existing = _get_state_row(oidc_key, db_path=db_path)
    if existing is None:
        return ElicitResult(False, ElicitState.FAILED, "No active session.")

    current_retries = existing.get("retry_count", 0)
    retries = current_retries + 1
    current_state = existing["state"]

    if retries > MAX_RETRIES:
        ss_queries.transition_state(oidc_key, ElicitState.FAILED.value, db_path=db_path)
        return ElicitResult(
            success=False,
            new_state=ElicitState.FAILED,
            message="Too many failed attempts. Start a new session.",
        )

    ss_queries.increment_retry_count(oidc_key, db_path=db_path)
    remaining = MAX_RETRIES - current_retries
    try:
        state_enum = ElicitState(current_state)
    except ValueError:
        state_enum = ElicitState.FAILED
    return ElicitResult(
        success=False,
        new_state=state_enum,
        message=f"Incorrect. {remaining} attempt(s) remaining.",
    )


def sweep_expired(db_path: Optional[str] = None) -> int:
    """Mark all expired sessions as EXPIRED. Returns count of swept sessions."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=TTL_SECONDS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.get_connection(db_path) as conn:
        cur = conn.execute(
            "UPDATE setup_state SET state = 'EXPIRED' "
            "WHERE updated_at < ? AND state NOT IN ('COMPLETED', 'FAILED', 'EXPIRED')",
            (cutoff,),
        )
        return cur.rowcount
