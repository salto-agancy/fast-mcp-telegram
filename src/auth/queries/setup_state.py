"""CRUD operations for the setup_state table (elicitation state machine)."""

import sqlite3

from src.auth.db import get_connection


def create_state(
    oidc_key: str,
    phone_number: str | None = None,
    db_path: str | None = None,
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


def transition_state(
    oidc_key: str,
    new_state: str,
    tg_code_hash: str | None = None,
    metadata: str | None = None,
    db_path: str | None = None,
) -> bool:
    """Transition to a new state, optionally updating code hash or metadata.

    All values are passed as bound parameters.

    Returns True if a row was updated, False otherwise.
    """
    set_clauses: list[str] = ["state = ?"]
    values: list[object] = [new_state]

    if tg_code_hash is not None:
        set_clauses.append("tg_code_hash = ?")
        values.append(tg_code_hash)

    if metadata is not None:
        set_clauses.append("metadata = ?")
        values.append(metadata)

    set_clauses.append("updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')")
    values.append(oidc_key)

    # SAFETY: Column names are hardcoded in SET clauses.
    # Sourcery false positive on dynamic SQL building.
    sql = f"UPDATE setup_state SET {', '.join(set_clauses)} WHERE oidc_key = ?"
    with get_connection(db_path) as conn:
        cursor = conn.execute(sql, values)
        return cursor.rowcount > 0


def get_all_active_states(db_path: str | None = None) -> list[sqlite3.Row]:
    """Return all non-COMPLETED, non-FAILED states (for active session tracking)."""
    with get_connection(db_path) as conn:
        return conn.execute(
            "SELECT * FROM setup_state WHERE state NOT IN ('COMPLETED', 'FAILED')"
        ).fetchall()


def increment_retry_count(
    oidc_key: str,
    db_path: str | None = None,
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


def get_state_row(oidc_key: str, db_path: str | None = None) -> dict | None:
    """Fetch a single setup_state row as a dict, or None if not found."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT oidc_key, state, phone_number, tg_code_hash, retry_count, "
            "metadata, created_at, updated_at "
            "FROM setup_state WHERE oidc_key = ?",
            (oidc_key,),
        ).fetchone()
        return dict(row) if row else None
