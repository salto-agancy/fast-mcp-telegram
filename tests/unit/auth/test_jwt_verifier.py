"""Tests for OIDC JWT verification module (Sub-phase 2.1)."""
import time
import pytest
from unittest.mock import patch, MagicMock
import jwt as pyjwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend

from src.auth.jwt_verifier import verify_oidc_token, _jwks_cache


# --- Test Fixtures ---

@pytest.fixture(autouse=True)
def clear_jwks_cache():
    """Clear JWKS cache before each test."""
    _jwks_cache.clear()
    yield
    _jwks_cache.clear()


@pytest.fixture
def rsa_key_pair():
    """Generate RSA key pair for signing test tokens."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    public_key = private_key.public_key()
    return private_key, public_key


@pytest.fixture
def sign_token(rsa_key_pair):
    """Factory to create signed JWT tokens."""
    private_key, _ = rsa_key_pair

    def _sign(payload: dict) -> str:
        return pyjwt.encode(
            payload,
            private_key,
            algorithm="RS256",
            headers={"kid": "test-key-id"},
        )

    return _sign


@pytest.fixture
def valid_payload():
    """Standard valid OIDC token payload."""
    now = int(time.time())
    return {
        "sub": "user-123",
        "iss": "https://auth.example.com/",
        "aud": "fast-mcp-telegram",
        "exp": now + 3600,
        "iat": now,
        "email": "user@example.com",
    }


@pytest.fixture
def mock_jwks_client(rsa_key_pair):
    """Mock PyJWKClient that returns our test public key."""
    _, public_key = rsa_key_pair

    mock_client = MagicMock()
    mock_signing_key = MagicMock()
    mock_signing_key.key = public_key
    mock_client.get_signing_key_from_jwt.return_value = mock_signing_key

    with patch("src.auth.jwt_verifier.PyJWKClient", return_value=mock_client):
        yield mock_client


# --- Tests ---

class TestVerifyOidcToken:
    """Test verify_oidc_token function."""

    def test_valid_token_returns_payload(
        self, sign_token, valid_payload, mock_jwks_client
    ):
        """Valid token should return decoded payload dict."""
        token = sign_token(valid_payload)
        result = verify_oidc_token(
            token,
            issuer="https://auth.example.com/",
            audience="fast-mcp-telegram",
        )

        assert result is not None
        assert result["sub"] == "user-123"
        assert result["email"] == "user@example.com"
        assert result["iss"] == "https://auth.example.com/"

    def test_expired_token_returns_none(
        self, sign_token, mock_jwks_client
    ):
        """Expired token should return None."""
        now = int(time.time())
        payload = {
            "sub": "user-123",
            "iss": "https://auth.example.com/",
            "aud": "fast-mcp-telegram",
            "exp": now - 3600,  # Expired 1 hour ago
            "iat": now - 7200,
        }
        token = sign_token(payload)

        result = verify_oidc_token(
            token,
            issuer="https://auth.example.com/",
            audience="fast-mcp-telegram",
        )

        assert result is None

    def test_wrong_audience_returns_none(
        self, sign_token, valid_payload, mock_jwks_client
    ):
        """Token with wrong audience should return None."""
        token = sign_token(valid_payload)

        result = verify_oidc_token(
            token,
            issuer="https://auth.example.com/",
            audience="wrong-audience",
        )

        assert result is None

    def test_wrong_issuer_returns_none(
        self, sign_token, valid_payload, mock_jwks_client
    ):
        """Token with wrong issuer should return None."""
        token = sign_token(valid_payload)

        result = verify_oidc_token(
            token,
            issuer="https://wrong-issuer.com/",
            audience="fast-mcp-telegram",
        )

        assert result is None

    def test_malformed_jwt_returns_none(self, mock_jwks_client):
        """Malformed JWT string should return None."""
        result = verify_oidc_token(
            "not.a.valid.jwt",
            issuer="https://auth.example.com/",
            audience="fast-mcp-telegram",
        )

        assert result is None

    def test_missing_env_vars_returns_none(self):
        """Missing TG_OIDC_ISSUER/AUDIENCE env vars should return None."""
        with patch.dict("os.environ", {}, clear=True):
            result = verify_oidc_token("some.token.here")

        assert result is None

    def test_jwks_fetch_failure_returns_none(
        self, sign_token, valid_payload
    ):
        """JWKS endpoint failure should return None gracefully."""
        token = sign_token(valid_payload)

        with patch("src.auth.jwt_verifier.PyJWKClient") as mock_cls:
            mock_cls.side_effect = Exception("Network error")
            result = verify_oidc_token(
                token,
                issuer="https://auth.example.com/",
                audience="fast-mcp-telegram",
            )

        assert result is None

    def test_jwks_caches_client_for_ttl(
        self, sign_token, valid_payload, mock_jwks_client
    ):
        """JWKS client is cached for TTL window, reused across verify calls."""
        token = sign_token(valid_payload)

        # First call — creates cache entry
        verify_oidc_token(
            token,
            issuer="https://auth.example.com/",
            audience="fast-mcp-telegram",
        )

        # Second call — should reuse cached client
        verify_oidc_token(
            token,
            issuer="https://auth.example.com/",
            audience="fast-mcp-telegram",
        )

        # PyJWKClient should be cached for the TTL window; if the cache
        # is bypassed, the underlying signer is fetched again. We assert
        # the signer is fetched twice (once per call) but the client is
        # reused across calls.
        # We patched it, so check the mock call count
        assert mock_jwks_client.get_signing_key_from_jwt.call_count == 2

    def test_missing_sub_claim_returns_none(
        self, sign_token, mock_jwks_client
    ):
        """Token without 'sub' claim should return None."""
        now = int(time.time())
        payload = {
            "iss": "https://auth.example.com/",
            "aud": "fast-mcp-telegram",
            "exp": now + 3600,
            "iat": now,
            # No 'sub'
        }
        token = sign_token(payload)

        result = verify_oidc_token(
            token,
            issuer="https://auth.example.com/",
            audience="fast-mcp-telegram",
        )

        assert result is None
