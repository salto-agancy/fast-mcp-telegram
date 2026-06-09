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
# setup_state CRUD — re-exported from queries module (source of truth)
# ---------------------------------------------------------------------------

from src.auth.queries.setup_state import (
    create_setup_state,
    get_setup_state,
    update_setup_state,
    expire_old_states,
)
