"""Tests for OIDC TokenVerifier adapter (Sub-phase 2.3)."""
import hashlib
import time
import pytest
from unittest.mock import patch, AsyncMock

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
import jwt as pyjwt

from src.auth.oauth_provider_adapter import OidcTokenVerifier
from src.auth.db import run_migrations
from src.auth.queries.oidc_identity import insert_identity


ISSUER = "https://auth.example.com/"
AUDIENCE = "fast-mcp-telegram"


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    run_migrations(db_file)
    # Ensure OIDC env vars are set for resolve_principal
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
def mock_jwks(rsa_key_pair):
    """Fresh PyJWKClient mock per test.
    
    Clears the module-level _jwks_cache to prevent stale mock instances
    from leaking between tests (the cache keys on issuer URL, so without
    clearing, test N+1 gets test N's mock which has wrong signing key).
    """
    _, public_key = rsa_key_pair
    from unittest.mock import MagicMock
    from src.auth.jwt_verifier import _jwks_cache
    _jwks_cache.clear()

    mock_client = MagicMock()
    mock_signing_key = MagicMock()
    mock_signing_key.key = public_key
    mock_client.get_signing_key_from_jwt.return_value = mock_signing_key
    with patch("src.auth.jwt_verifier.PyJWKClient", return_value=mock_client):
        yield mock_client
    _jwks_cache.clear()


def _make_key(sub: str, issuer: str) -> str:
    return hashlib.sha256(f"{sub}:{issuer}".encode()).hexdigest()[:32]


@pytest.fixture
def verifier(db):
    """Create a fresh OidcTokenVerifier per test with explicit issuer/audience."""
    return OidcTokenVerifier(issuer=ISSUER, audience=AUDIENCE, db_path=db)


class TestOidcTokenVerifier:

    @pytest.mark.asyncio
    async def test_valid_token_resolves_principal(
        self, sign_token, valid_payload, mock_jwks, db, verifier
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
        self, sign_token, valid_payload, mock_jwks, verifier
    ):
        token = sign_token(valid_payload)
        result = await verifier.verify_token(token)
        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_token_returns_none(self, mock_jwks, verifier):
        result = await verifier.verify_token("not.a.valid.jwt")
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_env_vars_returns_none(self, db, monkeypatch):
        """Verifies graceful degradation when OIDC config is absent.
        
        Uses its own verifier (no explicit issuer) to test env-var fallback path.
        Isolated from other tests — does NOT use the shared `verifier` fixture.
        """
        monkeypatch.delenv("TG_OIDC_ISSUER", raising=False)
        monkeypatch.delenv("TG_OIDC_AUDIENCE", raising=False)
        isolated_verifier = OidcTokenVerifier(db_path=db)
        result = await isolated_verifier.verify_token("any.token.here")
        assert result is None

    @pytest.mark.asyncio
    async def test_phone_fallback_when_no_username(
        self, sign_token, valid_payload, mock_jwks, tmp_path, monkeypatch
    ):
        """When username is NULL, principal resolves to +phone.
        
        Fully self-contained ��� uses own DB and env vars to avoid
        test-ordering pollution from test_missing_env_vars_returns_none.
        """
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

        v = OidcTokenVerifier(issuer=ISSUER, audience=AUDIENCE, db_path=db_file)
        token = sign_token(valid_payload)
        result = await v.verify_token(token)

        assert result is not None
        assert result.client_id == "+79991234567"

    @pytest.mark.asyncio
    async def test_user_id_fallback(
        self, sign_token, valid_payload, mock_jwks, tmp_path, monkeypatch
    ):
        """When both username and phone are NULL, principal resolves to user_id.
        
        Fully self-contained ��� uses own DB and env vars to avoid
        test-ordering pollution from test_missing_env_vars_returns_none.
        """
        monkeypatch.setenv("TG_OIDC_ISSUER", ISSUER)
        monkeypatch.setenv("TG_OIDC_AUDIENCE", AUDIENCE)
        db_file = str(tmp_path / "fallback_uid.db")
        run_migrations(db_file)

        key = _make_key("user-123", ISSUER)
        insert_identity(
            oidc_key=key, oidc_sub="user-123", oidc_issuer=ISSUER,
            telegram_user_id=300, db_path=db_file,
        )

        v = OidcTokenVerifier(issuer=ISSUER, audience=AUDIENCE, db_path=db_file)
        token = sign_token(valid_payload)
        result = await v.verify_token(token)

        assert result is not None
        assert result.client_id == "300"
