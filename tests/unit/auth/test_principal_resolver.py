"""Tests for principal resolver (Sub-phase 2.2)."""
import hashlib
import os
import pytest

from src.auth.principal_resolver import resolve_principal
from src.auth.db import run_migrations
from src.auth.queries.oidc_identity import insert_identity


@pytest.fixture
def db(tmp_path):
    """Create a temp DB with schema."""
    db_file = str(tmp_path / "test.db")
    run_migrations(db_file)
    return db_file


def _make_key(sub: str, issuer: str) -> str:
    return hashlib.sha256(f"{sub}:{issuer}".encode()).hexdigest()[:32]


class TestResolvePrincipal:
    ISSUER = "https://auth.example.com/"

    def test_resolve_by_username(self, db, monkeypatch):
        monkeypatch.setenv("TG_OIDC_ISSUER", self.ISSUER)
        key = _make_key("user-1", self.ISSUER)
        insert_identity(
            oidc_key=key, oidc_sub="user-1", oidc_issuer=self.ISSUER,
            telegram_user_id=100, telegram_username="alice",
            telegram_phone="79991234567", db_path=db,
        )

        result = resolve_principal("user-1", issuer=self.ISSUER, db_path=db)
        assert result == "@alice"

    def test_resolve_by_phone_when_no_username(self, db, monkeypatch):
        monkeypatch.setenv("TG_OIDC_ISSUER", self.ISSUER)
        key = _make_key("user-2", self.ISSUER)
        insert_identity(
            oidc_key=key, oidc_sub="user-2", oidc_issuer=self.ISSUER,
            telegram_user_id=200, telegram_phone="79998887766",
            db_path=db,
        )

        result = resolve_principal("user-2", issuer=self.ISSUER, db_path=db)
        assert result == "+79998887766"

    def test_resolve_by_user_id_fallback(self, db, monkeypatch):
        monkeypatch.setenv("TG_OIDC_ISSUER", self.ISSUER)
        key = _make_key("user-3", self.ISSUER)
        insert_identity(
            oidc_key=key, oidc_sub="user-3", oidc_issuer=self.ISSUER,
            telegram_user_id=300, db_path=db,
        )

        result = resolve_principal("user-3", issuer=self.ISSUER, db_path=db)
        assert result == "300"

    def test_unknown_sub_returns_none(self, db, monkeypatch):
        monkeypatch.setenv("TG_OIDC_ISSUER", self.ISSUER)
        result = resolve_principal("nonexistent", issuer=self.ISSUER, db_path=db)
        assert result is None

    def test_missing_issuer_env_returns_none(self, db, monkeypatch):
        monkeypatch.delenv("TG_OIDC_ISSUER", raising=False)
        result = resolve_principal("user-1", issuer=None, db_path=db)
        assert result is None

    def test_explicit_issuer_overrides_env(self, db, monkeypatch):
        monkeypatch.setenv("TG_OIDC_ISSUER", "https://wrong.example.com/")
        key = _make_key("user-4", self.ISSUER)
        insert_identity(
            oidc_key=key, oidc_sub="user-4", oidc_issuer=self.ISSUER,
            telegram_user_id=400, telegram_username="bob",
            db_path=db,
        )

        # Explicit issuer should be used for hashing, not env
        result = resolve_principal("user-4", issuer=self.ISSUER, db_path=db)
        assert result == "@bob"
