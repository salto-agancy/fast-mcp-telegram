"""Tests for OIDC storage layer: migrations and connection management."""

import sqlite3
from pathlib import Path

import pytest

from src.auth.db import get_connection, run_migrations


@pytest.fixture
def tmp_db(tmp_path: Path) -> str:
    """Create a temporary database path."""
    return str(tmp_path / "test_auth.db")


class TestMigrations:
    """Migration runner tests."""

    def test_migrations_create_tables(self, tmp_db: str) -> None:
        """All 4 tables exist after run_migrations()."""
        run_migrations(tmp_db)

        conn = sqlite3.connect(tmp_db)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()

        assert "oidc_identity" in tables
        assert "setup_state" in tables
        assert "schema_version" in tables

    def test_schema_version_tracking(self, tmp_db: str) -> None:
        """schema_version row inserted per migration."""
        run_migrations(tmp_db)

        conn = sqlite3.connect(tmp_db)
        rows = conn.execute(
            "SELECT version, description FROM schema_version ORDER BY version"
        ).fetchall()
        conn.close()

        assert len(rows) >= 1
        assert rows[0][0] == 1
        assert "001_initial_schema" in rows[0][1]

    def test_migration_idempotency(self, tmp_db: str) -> None:
        """Running migrations twice doesn't fail or duplicate rows."""
        run_migrations(tmp_db)
        run_migrations(tmp_db)  # Should not raise

        conn = sqlite3.connect(tmp_db)
        count = conn.execute(
            "SELECT COUNT(*) FROM schema_version WHERE version = 1"
        ).fetchone()[0]
        conn.close()

        assert count == 1


class TestConnection:
    """get_connection() context manager tests."""

    def test_get_connection_yields_connection(self, tmp_db: str) -> None:
        """Context manager yields a usable sqlite3.Connection."""
        run_migrations(tmp_db)

        with get_connection(tmp_db) as conn:
            result = conn.execute("SELECT 1").fetchone()
            assert result[0] == 1

    def test_get_connection_rollback_on_error(self, tmp_db: str) -> None:
        """Connection rolls back on exception."""
        run_migrations(tmp_db)

        with pytest.raises(ValueError):
            with get_connection(tmp_db) as conn:
                conn.execute(
                    "INSERT INTO oidc_identity (oidc_key, oidc_sub, oidc_issuer, telegram_user_id) VALUES (?, ?, ?, ?)",
                    ("k", "s", "i", 1),
                )
                raise ValueError("boom")

        # Row should NOT exist after rollback
        with get_connection(tmp_db) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM oidc_identity WHERE oidc_key = ?", ("k",)
            ).fetchone()
            assert row[0] == 0
