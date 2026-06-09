"""Tests for OIDC TokenVerifier adapter (Sub-phase 2.3)."""
import hashlib
import time
import pytest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
import jwt as pyjwt

from fastmcp.server.auth import JWTVerifier

from src.auth.oauth_provider_adapter import OidcTokenVerifier
from src.auth.db import run_migrations
from src.auth.queries.oidc_identity import insert_identity


ISSUER = "https://auth.example.com/"
AUDIENCE = "fast-mcp-telegram"


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    run_migrations(db_file)
    monkeypatch.setenv("TG_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("TG_OIDC_AUDIENCE", AUDIENCE)
    return db_file


@pytest.fixture
def rsa_key_pair():
    private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )
    return private_key, private_key.public_key()


@pytest.fixture
def public_key_pem(rsa_key_pair):
    """PEM-encoded public key for use with JWTVerifier(public_key=...)."""
    _, public_key = rsa_key_pair
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


@pytest.fixture
def sign_token(rsa_key_pair):
    private_key, _ = rsa_key_pair

    def _sign(payload: dict) -> str:
        return pyjwt.encode(
            payload, private_key, algorithm="RS256",
            headers={"kid": "test-key-id"},
        )
    return _sign


@pytest.fixture
def valid_payload():
    now = int(time.time())
    return {
        "sub": "user-123",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": now + 3600,
        "iat": now,
    }


@pytest.fixture
def jwt_verifier(public_key_pem):
    """Create a JWTVerifier with a static public key for test isolation.
    
    Uses a static PEM key instead of a JWKS URI, so no HTTP calls are made
    during verification. Tokens signed with the matching private key pass.
    """
    return JWTVerifier(
        public_key=public_key_pem,
        issuer=ISSUER,
        audience=AUDIENCE,
        algorithm="RS256",
    )


@pytest.fixture
def verifier(db, jwt_verifier):
    """OidcTokenVerifier with injected test JWTVerifier."""
    return OidcTokenVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        db_path=db,
        jwt_verifier=jwt_verifier,
    )


def _make_key(sub: str, issuer: str) -> str:
    return hashlib.sha256(f"{sub}:{issuer}".encode()).hexdigest()[:32]


class TestOidcTokenVerifier:

    @pytest.mark.asyncio
    async def test_valid_token_resolves_principal(
        self, sign_token, valid_payload, db, verifier
    ):
        key = _make_key("user-123", ISSUER)
        insert_identity(
            oidc_key=key, oidc_sub="user-123", oidc_issuer=ISSUER,
            telegram_user_id=100, telegram_username="alice",
            db_path=db,
        )

        token = sign_token(valid_payload)
        result = await verifier.verify_token(token)

        assert result is not None
        assert result.client_id == "@alice"
        assert "read" in result.scopes
        assert "write" in result.scopes

    @pytest.mark.asyncio
    async def test_unknown_sub_returns_none(
        self, sign_token, valid_payload, verifier
    ):
        token = sign_token(valid_payload)
        result = await verifier.verify_token(token)
        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_token_returns_none(self, verifier):
        result = await verifier.verify_token("not.a.valid.jwt")
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_env_vars_returns_none(self, db, monkeypatch):
        monkeypatch.delenv("TG_OIDC_ISSUER", raising=False)
        monkeypatch.delenv("TG_OIDC_AUDIENCE", raising=False)
        isolated_verifier = OidcTokenVerifier(db_path=db)
        result = await isolated_verifier.verify_token("any.token.here")
        assert result is None

    @pytest.mark.asyncio
    async def test_phone_fallback_when_no_username(
        self, sign_token, valid_payload, public_key_pem,
        tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("TG_OIDC_ISSUER", ISSUER)
        monkeypatch.setenv("TG_OIDC_AUDIENCE", AUDIENCE)
        db_file = str(tmp_path / "fallback_phone.db")
        run_migrations(db_file)

        key = _make_key("user-123", ISSUER)
        insert_identity(
            oidc_key=key, oidc_sub="user-123", oidc_issuer=ISSUER,
            telegram_user_id=200, telegram_phone="79991234567",
            db_path=db_file,
        )

        jwv = JWTVerifier(
            public_key=public_key_pem,
            issuer=ISSUER, audience=AUDIENCE, algorithm="RS256",
        )
        v = OidcTokenVerifier(
            issuer=ISSUER, audience=AUDIENCE,
            db_path=db_file, jwt_verifier=jwv,
        )
        token = sign_token(valid_payload)
        result = await v.verify_token(token)

        assert result is not None
        assert result.client_id == "+79991234567"

    @pytest.mark.asyncio
    async def test_user_id_fallback(
        self, sign_token, valid_payload, public_key_pem,
        tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("TG_OIDC_ISSUER", ISSUER)
        monkeypatch.setenv("TG_OIDC_AUDIENCE", AUDIENCE)
        db_file = str(tmp_path / "fallback_uid.db")
        run_migrations(db_file)

        key = _make_key("user-123", ISSUER)
        insert_identity(
            oidc_key=key, oidc_sub="user-123", oidc_issuer=ISSUER,
            telegram_user_id=300, db_path=db_file,
        )

        jwv = JWTVerifier(
            public_key=public_key_pem,
            issuer=ISSUER, audience=AUDIENCE, algorithm="RS256",
        )
        v = OidcTokenVerifier(
            issuer=ISSUER, audience=AUDIENCE,
            db_path=db_file, jwt_verifier=jwv,
        )
        token = sign_token(valid_payload)
        result = await v.verify_token(token)

        assert result is not None
        assert result.client_id == "300"

    # --- JWT verification edge cases (replaced former test_jwt_verifier scenarios) ---

    @pytest.mark.asyncio
    async def test_expired_token_returns_none(
        self, sign_token, public_key_pem, db,
    ):
        """Expired OIDC JWT should be rejected by JWTVerifier."""
        now = int(time.time())
        expired = {
            "sub": "user-123",
            "iss": ISSUER,
            "aud": AUDIENCE,
            "exp": now - 3600,
            "iat": now - 7200,
        }
        token = sign_token(expired)
        jwv = JWTVerifier(
            public_key=public_key_pem,
            issuer=ISSUER, audience=AUDIENCE, algorithm="RS256",
        )
        v = OidcTokenVerifier(
            issuer=ISSUER, audience=AUDIENCE, db_path=db, jwt_verifier=jwv,
        )
        result = await v.verify_token(token)
        assert result is None

    @pytest.mark.asyncio
    async def test_wrong_audience_returns_none(
        self, public_key_pem, sign_token, valid_payload, db,
    ):
        """Token with mismatched audience should be rejected by JWTVerifier."""
        jwv = JWTVerifier(
            public_key=public_key_pem,
            issuer=ISSUER, audience="wrong-audience", algorithm="RS256",
        )
        v = OidcTokenVerifier(
            issuer=ISSUER, audience=AUDIENCE, db_path=db, jwt_verifier=jwv,
        )
        token = sign_token(valid_payload)
        result = await v.verify_token(token)
        assert result is None

    @pytest.mark.asyncio
    async def test_wrong_issuer_returns_none(
        self, public_key_pem, sign_token, valid_payload, db,
    ):
        """Token with mismatched issuer should be rejected by JWTVerifier."""
        jwv = JWTVerifier(
            public_key=public_key_pem,
            issuer="https://wrong-issuer.com/",
            audience=AUDIENCE, algorithm="RS256",
        )
        v = OidcTokenVerifier(
            issuer=ISSUER, audience=AUDIENCE, db_path=db, jwt_verifier=jwv,
        )
        token = sign_token(valid_payload)
        result = await v.verify_token(token)
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_sub_claim_returns_none(
        self, sign_token, public_key_pem, db,
    ):
        """Token without 'sub' claim passes JWTVerifier but adapter rejects."""
        now = int(time.time())
        payload = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "exp": now + 3600,
            "iat": now,
        }
        token = sign_token(payload)
        jwv = JWTVerifier(
            public_key=public_key_pem,
            issuer=ISSUER, audience=AUDIENCE, algorithm="RS256",
        )
        v = OidcTokenVerifier(
            issuer=ISSUER, audience=AUDIENCE, db_path=db, jwt_verifier=jwv,
        )
        result = await v.verify_token(token)
        assert result is None
