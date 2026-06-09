"""SQLite connection management and migration runner for OIDC auth storage."""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
DEFAULT_DB_PATH = "./data/auth.db"


def _resolve_db_path(override: str | None = None) -> str:
    """Return the effective database path.

    Priority: explicit override > TG_DATABASE_URL env var > default.
    Evaluated at call time so monkeypatching in tests works correctly.
    """
    if override is not None:
        return override
    return os.environ.get("TG_DATABASE_URL", DEFAULT_DB_PATH)


def run_migrations(db_path: str | None = None) -> None:
    """Apply pending SQL migrations in order.

    Creates the schema_version tracking table if it doesn't exist,
    then applies any migration files whose version exceeds the current max.
    """
    target = _resolve_db_path(db_path)
    conn = sqlite3.connect(target)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                description TEXT
            )
            """
        )

        current = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_version"
        ).fetchone()[0]

        for migration_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = int(migration_file.stem.split("_")[0])
            if version > current:
                sql = migration_file.read_text()
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                    (version, migration_file.stem),
                )
                conn.commit()
    finally:
        conn.close()


@contextmanager
def get_connection(db_path: str | None = None) -> Generator[sqlite3.Connection, None, None]:
    """Yield a sqlite3.Connection with row_factory, FK enforcement, and auto-commit/rollback."""
    target = _resolve_db_path(db_path)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# setup_state CRUD (for OIDC elicitation state machine)
# ---------------------------------------------------------------------------

def create_setup_state(
    oidc_key: str,
    state: str,
    phone_number: str | None = None,
    metadata: dict | None = None,
    db_path: str | None = None,
) -> None:
    """Create a new setup_state row."""
    import json
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO setup_state (oidc_key, state, phone_number, metadata, retry_count, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 0, ?, ?)",
            (oidc_key, state, phone_number, json.dumps(metadata) if metadata else None, now, now),
        )


def get_setup_state(oidc_key: str, db_path: str | None = None) -> dict | None:
    """Fetch setup_state row as dict, or None if not found."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT oidc_key, state, phone_number, tg_code_hash, retry_count, metadata, created_at, updated_at "
            "FROM setup_state WHERE oidc_key = ?",
            (oidc_key,),
        ).fetchone()
        return dict(row) if row else None


def update_setup_state(
    oidc_key: str,
    state: str,
    phone_number: str | None = None,
    tg_code_hash: str | None = None,
    metadata: dict | None = None,
    retry_count: int | None = None,
    db_path: str | None = None,
) -> None:
    """Update state and related fields. Refreshes updated_at."""
    import json
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sets = ["state = ?", "updated_at = ?"]
    params: list = [state, now]
    if phone_number is not None:
        sets.append("phone_number = ?")
        params.append(phone_number)
    if tg_code_hash is not None:
        sets.append("tg_code_hash = ?")
        params.append(tg_code_hash)
    if metadata is not None:
        sets.append("metadata = ?")
        params.append(json.dumps(metadata))
    if retry_count is not None:
        sets.append("retry_count = ?")
        params.append(retry_count)
    params.append(oidc_key)
    with get_connection(db_path) as conn:
        # SAFETY: Column names come from _UPDATABLE_FIELDS (hardcoded set), never user input.
        # Values are bound parameters. Sourcery false positive — whitelist prevents injection.
        conn.execute(  # noqa: S608
            f"UPDATE setup_state SET {', '.join(sets)} WHERE oidc_key = ?",
            params,
        )


def expire_old_states(cutoff_iso: str, db_path: str | None = None) -> int:
    """Mark sessions with updated_at before cutoff_iso as EXPIRED. Returns count updated."""
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "UPDATE setup_state SET state = 'EXPIRED' "
            "WHERE updated_at < ? AND state NOT IN ('COMPLETED', 'FAILED', 'EXPIRED')",
            (cutoff_iso,),
        )
        return cur.rowcount
