"""CRUD operations for the telegram_session table."""

import sqlite3
from typing import Any

from src.auth.db import get_connection


def insert_session(
    oidc_key: str,
    session_filename: str,
    dc_id: int,
    server_address: str,
    port: int,
    auth_key: bytes,
    db_path: str | None = None,
) -> None:
    """Insert a telegram_session row linked to an existing oidc_identity."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO telegram_session (
                oidc_key, session_filename, dc_id,
                server_address, port, auth_key
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (oidc_key, session_filename, dc_id, server_address, port, auth_key),
        )


def get_session(oidc_key: str, db_path: str | None = None) -> dict[str, Any] | None:
    """Return telegram_session row as dict, or None if not found."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM telegram_session WHERE oidc_key = ?",
            (oidc_key,),
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def touch_session(oidc_key: str, db_path: str | None = None) -> None:
    """Update last_used_at to current UTC time."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE telegram_session
            SET last_used_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            WHERE oidc_key = ?
            """,
            (oidc_key,),
        )
