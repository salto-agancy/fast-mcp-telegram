"""CRUD operations for the setup_state table (elicitation state machine)."""

import sqlite3
from typing import Optional

from src.auth.db import get_connection


def create_state(
    oidc_key: str,
    phone_number: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    """Create initial WAITING_PHONE state for an OIDC key."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO setup_state (oidc_key, state, phone_number, retry_count)
            VALUES (?, 'WAITING_PHONE', ?, 0)
            """,
            (oidc_key, phone_number),
        )


_TRANSITIONABLE_FIELDS = {
    "tg_code_hash",
    "metadata",
}


def transition_state(
    oidc_key: str,
    new_state: str,
    tg_code_hash: Optional[str] = None,
    metadata: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    """Transition to a new state, optionally updating code hash or metadata.

    Uses a whitelist of allowed column names to prevent SQL injection.
    All values are passed as bound parameters.
    """
    updates: dict[str, object] = {"state": new_state}
    if tg_code_hash is not None:
        updates["tg_code_hash"] = tg_code_hash
    if metadata is not None:
        updates["metadata"] = metadata

    # Whitelist check for optional fields
    for col in updates:
        if col not in _TRANSITIONABLE_FIELDS and col != "state":
            raise ValueError(f"Invalid column name: {col}")

    set_clauses = [f"{col} = ?" for col in updates]
    set_clauses.append("updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')")
    values = list(updates.values()) + [oidc_key]

    sql = f"UPDATE setup_state SET {', '.join(set_clauses)} WHERE oidc_key = ?"
    with get_connection(db_path) as conn:
        conn.execute(sql, values)


def get_active_states(
    older_than_seconds: int = 0,
    db_path: Optional[str] = None,
) -> list[sqlite3.Row]:
    """Return all setup states.

    If older_than_seconds > 0, only return rows whose updated_at is older
    than that many seconds from now (for TTL sweep).
    If older_than_seconds == 0, return all non-COMPLETED/FAILED states.
    """
    with get_connection(db_path) as conn:
        if older_than_seconds > 0:
            return conn.execute(
                """
                SELECT * FROM setup_state
                WHERE updated_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ? || ' seconds')
                """,
                (f"-{older_than_seconds}",),
            ).fetchall()
        else:
            return conn.execute(
                "SELECT * FROM setup_state WHERE state NOT IN ('COMPLETED', 'FAILED')"
            ).fetchall()


def delete_expired(
    older_than_seconds: int,
    db_path: Optional[str] = None,
) -> int:
    """Delete states older than threshold. Returns number of deleted rows."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            DELETE FROM setup_state
            WHERE updated_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ? || ' seconds')
            """,
            (f"-{older_than_seconds}",),
        )
        return cursor.rowcount


def increment_retry_count(
    oidc_key: str,
    db_path: Optional[str] = None,
) -> None:
    """Increment retry_count and refresh updated_at."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE setup_state
            SET retry_count = retry_count + 1,
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            WHERE oidc_key = ?
            """,
            (oidc_key,),
        )
