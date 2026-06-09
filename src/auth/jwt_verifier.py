"""OIDC JWT verification using PyJWT + JWKS caching."""
import os
import time
import logging
from typing import Optional

import jwt
from jwt import PyJWKClient

logger = logging.getLogger(__name__)

_jwks_cache: dict[str, tuple[PyJWKClient, float]] = {}
_CACHE_TTL = 3600  # 1 hour


def _get_jwks_client(issuer: str) -> PyJWKClient:
    """Get or create a cached JWKS client for the issuer."""
    now = time.time()
    if issuer in _jwks_cache:
        client, cached_at = _jwks_cache[issuer]
        if now - cached_at < _CACHE_TTL:
            return client

    jwks_uri = issuer.rstrip("/") + "/.well-known/jwks.json"
    client = PyJWKClient(jwks_uri)
    _jwks_cache[issuer] = (client, now)
    return client


def verify_oidc_token(
    token: str,
    issuer: Optional[str] = None,
    audience: Optional[str] = None,
) -> Optional[dict]:
    """Verify an OIDC JWT token and return decoded payload.

    Args:
        token: The JWT bearer token.
        issuer: Expected issuer URL. Defaults to TG_OIDC_ISSUER env var.
        audience: Expected audience claim. Defaults to TG_OIDC_AUDIENCE env var.

    Returns:
        Decoded JWT payload dict on success, None on any failure.
    """
    issuer = issuer or os.environ.get("TG_OIDC_ISSUER")
    audience = audience or os.environ.get("TG_OIDC_AUDIENCE")

    if not issuer or not audience:
        logger.warning("OIDC verification skipped: TG_OIDC_ISSUER or TG_OIDC_AUDIENCE not set")
        return None

    try:
        jwks_client = _get_jwks_client(issuer)
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
            issuer=issuer,
            audience=audience,
            options={
                "require": ["exp", "iat", "iss", "aud", "sub"],
                "verify_exp": True,
                "verify_iat": True,
                "verify_iss": True,
                "verify_aud": True,
            },
        )
        return payload

    except jwt.ExpiredSignatureError:
        logger.debug("OIDC token expired")
        return None
    except jwt.InvalidAudienceError:
        logger.debug("OIDC token audience mismatch")
        return None
    except jwt.InvalidIssuerError:
        logger.debug("OIDC token issuer mismatch")
        return None
    except jwt.PyJWTError as e:
        logger.debug("OIDC token verification failed: %s", e)
        return None
    except Exception as e:
        logger.warning("OIDC JWKS fetch or unexpected error: %s", e)
        return None
