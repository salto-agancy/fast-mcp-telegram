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
    expected_state: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
    db_path: Optional[str] = None,
) -> bool:
    """Transition to a new state, optionally updating code hash or metadata.

    When ``expected_state`` is set, the UPDATE only applies if current
    state matches (prevents racing with concurrent elicitation flows).

    When ``ttl_seconds`` is set, the UPDATE only applies if the row was
    updated within the last ``ttl_seconds`` — expired sessions are
    rejected at the DB level without a separate SELECT.

    Uses a whitelist of allowed column names to prevent SQL injection.
    All values are passed as bound parameters.

    Returns True if a row was updated, False otherwise.
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
    values = list(updates.values())

    where_clauses: list[str] = []
    where_values: list[object] = []
    if expected_state is not None:
        where_clauses.append("state = ?")
        where_values.append(expected_state)
    if ttl_seconds is not None:
        where_clauses.append("updated_at >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ? || ' seconds')")
        where_values.append(f"-{ttl_seconds}")

    where_clauses.append("oidc_key = ?")
    where_values.append(oidc_key)

    all_values = values + where_values

    # SAFETY: Column names come from _TRANSITIONABLE_FIELDS (hardcoded set).
    # WHERE conditions are bound parameters. Sourcery false positive.
    sql = (
        f"UPDATE setup_state SET {', '.join(set_clauses)} "
        f"WHERE {' AND '.join(where_clauses)}"
    )  # noqa: S608
    with get_connection(db_path) as conn:
        cursor = conn.execute(sql, all_values)
        return cursor.rowcount > 0


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
