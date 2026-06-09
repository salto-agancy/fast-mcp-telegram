"""FastMCP TokenVerifier adapter for OIDC self-service auth."""

import logging
import os

from fastmcp.server.auth import AccessToken, JWTVerifier, TokenVerifier

from src.auth.principal_resolver import resolve_principal

logger = logging.getLogger(__name__)


class OidcTokenVerifier(TokenVerifier):
    """OIDC token verifier that resolves Telegram ACL principals.

    Wires FastMCP's built-in JWTVerifier for token validation, then delegates
    principal resolution to resolve_principal() for the Telegram identity mapping.
    Returns an AccessToken with the Telegram identity as the principal/client_id.
    """

    def __init__(
        self,
        *,
        issuer: str | None = None,
        audience: str | None = None,
        db_path: str | None = None,
        algorithm: str | None = None,
        jwt_verifier: JWTVerifier | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.issuer = issuer or os.environ.get("TG_OIDC_ISSUER", "")
        self.audience = audience or os.environ.get("TG_OIDC_AUDIENCE", "")
        self.db_path = db_path

        # Allow dependency injection for testing; otherwise create JWTVerifier
        # from the issuer's well-known JWKS endpoint.
        self._jwt_verifier = jwt_verifier or (
            JWTVerifier(
                jwks_uri=self.issuer.rstrip("/") + "/.well-known/jwks.json",
                issuer=self.issuer,
                audience=self.audience,
                algorithm=algorithm or "RS256",
            )
            if self.issuer
            else None
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify OIDC JWT and resolve to Telegram principal.

        Returns:
            AccessToken with principal=telegram_identity on success.
            None if token invalid, env vars missing, or no identity mapping.
        """
        if not self.issuer or not self.audience or not self._jwt_verifier:
            logger.debug("OIDC verifier skipped: issuer/audience not configured")
            return None

        access_token = await self._jwt_verifier.verify_token(token)
        if access_token is None:
            return None

        oidc_sub = access_token.claims.get("sub")
        if not oidc_sub:
            logger.warning("OIDC token valid but missing 'sub' claim")
            return None

        principal = resolve_principal(
            oidc_sub, issuer=self.issuer, db_path=self.db_path
        )

        if principal is None:
            # No mapping yet — returns None (indistinguishable from invalid token).
            # Elicitation tools handle first-time setup separately.
            logger.info(
                "OIDC sub=%s has no Telegram mapping; elicitation needed", oidc_sub
            )
            return None

        return AccessToken(
            token=token,
            client_id=principal,
            scopes=["read", "write"],
        )
