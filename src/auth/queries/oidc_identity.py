"""CRUD operations for the oidc_identity table."""

import sqlite3
from typing import Optional

from src.auth.db import get_connection


def insert_identity(
    oidc_key: str,
    oidc_sub: str,
    oidc_issuer: str,
    telegram_user_id: int,
    telegram_username: Optional[str] = None,
    telegram_phone: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    """Insert a new OIDC identity mapping.

    Raises sqlite3.IntegrityError if oidc_key already exists.
    """
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO oidc_identity
                (oidc_key, oidc_sub, oidc_issuer, telegram_user_id, telegram_username, telegram_phone)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (oidc_key, oidc_sub, oidc_issuer, telegram_user_id, telegram_username, telegram_phone),
        )


def get_identity(oidc_key: str, db_path: Optional[str] = None) -> Optional[sqlite3.Row]:
    """Retrieve an OIDC identity by key. Returns None if not found."""
    with get_connection(db_path) as conn:
        return conn.execute(
            "SELECT * FROM oidc_identity WHERE oidc_key = ?",
            (oidc_key,),
        ).fetchone()


def update_identity(
    oidc_key: str,
    telegram_username: Optional[str] = None,
    telegram_phone: Optional[str] = None,
    telegram_user_id: Optional[int] = None,
    db_path: Optional[str] = None,
) -> None:
    """Update Telegram identity fields and refresh updated_at timestamp."""
    fields = []
    values: list = []

    if telegram_username is not None:
        fields.append("telegram_username = ?")
        values.append(telegram_username)
    if telegram_phone is not None:
        fields.append("telegram_phone = ?")
        values.append(telegram_phone)
    if telegram_user_id is not None:
        fields.append("telegram_user_id = ?")
        values.append(telegram_user_id)

    if not fields:
        return

    fields.append("updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')")
    values.append(oidc_key)

    with get_connection(db_path) as conn:
        conn.execute(
            f"UPDATE oidc_identity SET {', '.join(fields)} WHERE oidc_key = ?",
            values,
        )
