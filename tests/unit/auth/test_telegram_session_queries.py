"""Tests for telegram_session metadata CRUD and legacy migration script."""

import hashlib
from pathlib import Path

import pytest
import yaml

from src.auth.db import get_connection, run_migrations
from src.auth.queries.oidc_identity import insert_identity
from src.auth.queries.telegram_session import (
    get_session,
    insert_session,
    touch_session,
)


@pytest.fixture
def db(tmp_path: Path) -> str:
    """Create and migrate a temporary database."""
    path = str(tmp_path / "test_tg_session.db")
    run_migrations(path)
    return path


@pytest.fixture
def oidc_key_with_identity(db: str) -> str:
    """Insert an oidc_identity row so FK constraint is satisfied."""
    key = "tg_sess_test_key"
    insert_identity(
        oidc_key=key,
        oidc_sub="sub-tg-sess",
        oidc_issuer="https://example.com/",
        telegram_user_id=777,
        telegram_username="sessuser",
        db_path=db,
    )
    return key


class TestTelegramSessionCRUD:
    """telegram_session table operations."""

    def test_insert_telegram_session(self, db: str, oidc_key_with_identity: str) -> None:
        """Links to oidc_identity via FK, stores all fields."""
        insert_session(
            oidc_key=oidc_key_with_identity,
            session_filename="abcd1234.session",
            dc_id=2,
            server_address="149.154.167.50",
            port=443,
            auth_key=b"\x00" * 256,
            db_path=db,
        )

        row = get_session(oidc_key_with_identity, db_path=db)
        assert row is not None
        assert row["session_filename"] == "abcd1234.session"
        assert row["dc_id"] == 2
        assert row["server_address"] == "149.154.167.50"
        assert row["port"] == 443
        assert row["auth_key"] == b"\x00" * 256

    def test_get_session_missing(self, db: str) -> None:
        """Returns None for non-existent oidc_key."""
        row = get_session("nonexistent", db_path=db)
        assert row is None

    def test_update_last_used(self, db: str, oidc_key_with_identity: str) -> None:
        """touch_session updates last_used_at timestamp."""
        insert_session(
            oidc_key=oidc_key_with_identity,
            session_filename="touch.session",
            dc_id=1,
            server_address="1.2.3.4",
            port=443,
            auth_key=b"\x01" * 256,
            db_path=db,
        )

        # Capture initial timestamp
        row_before = get_session(oidc_key_with_identity, db_path=db)
        initial_ts = row_before["last_used_at"]

        # Manually backdate so touch produces a different value
        with get_connection(db) as conn:
            conn.execute(
                "UPDATE telegram_session SET last_used_at = '2020-01-01T00:00:00Z' WHERE oidc_key = ?",
                (oidc_key_with_identity,),
            )

        touch_session(oidc_key_with_identity, db_path=db)

        row_after = get_session(oidc_key_with_identity, db_path=db)
        assert row_after["last_used_at"] != "2020-01-01T00:00:00Z"
        assert row_after["last_used_at"] >= initial_ts

    def test_fk_constraint_rejects_orphan(self, db: str) -> None:
        """Cannot insert telegram_session without matching oidc_identity."""
        import sqlite3

        with pytest.raises(sqlite3.IntegrityError):
            insert_session(
                oidc_key="no_such_identity",
                session_filename="orphan.session",
                dc_id=1,
                server_address="1.2.3.4",
                port=443,
                auth_key=b"\x00" * 256,
                db_path=db,
            )


class TestLegacyMigrationScript:
    """scripts/migrate_legacy.py reads YAML and inserts placeholder rows."""

    def test_migrate_legacy_script(self, db: str, tmp_path: Path) -> None:
        """Reads bearer→telegram mapping YAML, inserts placeholder oidc_identity rows."""
        # Create sample legacy_tokens.yaml
        legacy_yaml = tmp_path / "legacy_tokens.yaml"
        legacy_data = {
            "tokens": [
                {
                    "bearer_prefix": "abc123",
                    "telegram_user_id": 999,
                    "telegram_username": "legacyuser",
                },
                {
                    "bearer_prefix": "def456",
                    "telegram_user_id": 888,
                    "telegram_phone": "1555000111",
                },
            ]
        }
        legacy_yaml.write_text(yaml.dump(legacy_data))

        # Run the migration script
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "scripts/migrate_legacy.py",
                "--bearer-map",
                str(legacy_yaml),
                "--db",
                db,
            ],
            capture_output=True,
            text=True,
            cwd="/root/fast-mcp-telegram",
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"

        # Verify rows were inserted
        from src.auth.queries.oidc_identity import get_identity

        # Placeholder oidc_key is sha256 of bearer token prefix
        key1 = hashlib.sha256(b"abc123").hexdigest()[:32]
        row = get_identity(key1, db_path=db)
        assert row is not None
        assert row["telegram_user_id"] == 999
        assert row["telegram_username"] == "legacyuser"
        assert row["oidc_sub"] == "LEGACY_PLACEHOLDER"

        key2 = hashlib.sha256(b"def456").hexdigest()[:32]
        row2 = get_identity(key2, db_path=db)
        assert row2 is not None
        assert row2["telegram_user_id"] == 888
        assert row2["telegram_phone"] == "1555000111"
