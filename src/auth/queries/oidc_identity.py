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


def get_identity(oidc_key: str, db_path: Optional[str] = None) -> Optional[dict]:
    """Retrieve an OIDC identity by key. Returns dict or None if not found."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM oidc_identity WHERE oidc_key = ?",
            (oidc_key,),
        ).fetchone()
        return dict(row) if row else None


_UPDATABLE_FIELDS = {
    "telegram_username",
    "telegram_phone",
    "telegram_user_id",
}


def update_identity(
    oidc_key: str,
    telegram_username: Optional[str] = None,
    telegram_phone: Optional[str] = None,
    telegram_user_id: Optional[int] = None,
    db_path: Optional[str] = None,
) -> None:
    """Update Telegram identity fields and refresh updated_at timestamp.

    Uses a whitelist of allowed column names to prevent SQL injection.
    All values are passed as bound parameters.
    """
    updates: dict[str, object] = {}
    if telegram_username is not None:
        updates["telegram_username"] = telegram_username
    if telegram_phone is not None:
        updates["telegram_phone"] = telegram_phone
    if telegram_user_id is not None:
        updates["telegram_user_id"] = telegram_user_id

    if not updates:
        return

    # Whitelist check: only allow known column names
    for col in updates:
        if col not in _UPDATABLE_FIELDS:
            raise ValueError(f"Invalid column name: {col}")

    set_clauses = [f"{col} = ?" for col in updates]
    set_clauses.append("updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')")
    values = list(updates.values()) + [oidc_key]

    sql = f"UPDATE oidc_identity SET {', '.join(set_clauses)} WHERE oidc_key = ?"
    with get_connection(db_path) as conn:
        conn.execute(sql, values)
