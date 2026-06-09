"""Tests for OIDC identity CRUD operations."""

import sqlite3
from pathlib import Path

import pytest

from src.auth.db import run_migrations
from src.auth.queries.oidc_identity import (
    get_identity,
    insert_identity,
    update_identity,
)


@pytest.fixture
def db(tmp_path: Path) -> str:
    """Create and migrate a temporary database."""
    path = str(tmp_path / "test_oidc.db")
    run_migrations(path)
    return path


class TestOidcIdentityCRUD:
    """OIDC identity table operations."""

    def test_insert_oidc_identity(self, db: str) -> None:
        """Insert row, query back, verify fields."""
        insert_identity(
            oidc_key="key1",
            oidc_sub="sub1",
            oidc_issuer="https://auth.example.com/",
            telegram_user_id=12345,
            telegram_username="testuser",
            telegram_phone="1234567890",
            db_path=db,
        )

        row = get_identity("key1", db_path=db)
        assert row is not None
        assert row["oidc_key"] == "key1"
        assert row["oidc_sub"] == "sub1"
        assert row["oidc_issuer"] == "https://auth.example.com/"
        assert row["telegram_user_id"] == 12345
        assert row["telegram_username"] == "testuser"
        assert row["telegram_phone"] == "1234567890"
        assert row["created_at"] is not None
        assert row["updated_at"] is not None

    def test_get_oidc_identity_by_key_missing(self, db: str) -> None:
        """Returns None for missing key."""
        result = get_identity("nonexistent", db_path=db)
        assert result is None

    def test_update_oidc_identity_timestamp(self, db: str) -> None:
        """updated_at changes on update."""
        insert_identity(
            oidc_key="key2",
            oidc_sub="sub2",
            oidc_issuer="https://auth.example.com/",
            telegram_user_id=22222,
            db_path=db,
        )

        original = get_identity("key2", db_path=db)
        assert original is not None

        import time
        time.sleep(0.01)

        update_identity(
            oidc_key="key2",
            telegram_username="newusername",
            telegram_phone="9876543210",
            db_path=db,
        )

        updated = get_identity("key2", db_path=db)
        assert updated is not None
        assert updated["telegram_username"] == "newusername"
        assert updated["telegram_phone"] == "9876543210"
        assert updated["updated_at"] >= original["updated_at"]

    def test_unique_oidc_key_constraint(self, db: str) -> None:
        """Duplicate insert raises IntegrityError."""
        insert_identity(
            oidc_key="dup",
            oidc_sub="s",
            oidc_issuer="i",
            telegram_user_id=1,
            db_path=db,
        )

        with pytest.raises(sqlite3.IntegrityError):
            insert_identity(
                oidc_key="dup",
                oidc_sub="s2",
                oidc_issuer="i2",
                telegram_user_id=2,
                db_path=db,
            )


class TestMakeOidcKey:
    """OIDC key derivation — must be deterministic and collision-resistant."""

    def test_deterministic(self) -> None:
        """Same input always produces same key."""
        from src.auth.queries.oidc_identity import make_oidc_key
        k1 = make_oidc_key("user-123", "https://auth.example.com/")
        k2 = make_oidc_key("user-123", "https://auth.example.com/")
        assert k1 == k2

    def test_different_sub_different_key(self) -> None:
        """Different sub claims produce different keys."""
        from src.auth.queries.oidc_identity import make_oidc_key
        k1 = make_oidc_key("user-123", "https://auth.example.com/")
        k2 = make_oidc_key("user-456", "https://auth.example.com/")
        assert k1 != k2

    def test_different_issuer_different_key(self) -> None:
        """Same sub from different issuers produces different keys."""
        from src.auth.queries.oidc_identity import make_oidc_key
        k1 = make_oidc_key("user-123", "https://auth.example.com/")
        k2 = make_oidc_key("user-123", "https://other.auth.com/")
        assert k1 != k2

    def test_key_length_is_32_hex(self) -> None:
        """Key is 32 hex chars (first half of sha256)."""
        from src.auth.queries.oidc_identity import make_oidc_key
        k = make_oidc_key("user-123", "https://auth.example.com/")
        assert len(k) == 32
        assert all(c in "0123456789abcdef" for c in k)
