"""FastMCP TokenVerifier adapter for OIDC self-service auth."""
import logging
import os
from typing import Optional

from fastmcp.server.auth import AccessToken, TokenVerifier

from src.auth.jwt_verifier import verify_oidc_token
from src.auth.principal_resolver import resolve_principal

logger = logging.getLogger(__name__)


class OidcTokenVerifier(TokenVerifier):
    """OIDC token verifier that resolves Telegram ACL principals.

    Wires JWT verification → principal resolution into FastMCP's auth pipeline.
    Returns an AccessToken with the Telegram identity as the principal/client_id.
    If no mapping exists, returns None (elicitation flow handled at middleware level).
    """

    def __init__(
        self,
        *,
        issuer: Optional[str] = None,
        audience: Optional[str] = None,
        db_path: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.issuer = issuer or os.environ.get("TG_OIDC_ISSUER", "")
        self.audience = audience or os.environ.get("TG_OIDC_AUDIENCE", "")
        self.db_path = db_path

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        """Verify OIDC JWT and resolve to Telegram principal.

        Returns:
            AccessToken with principal=telegram_identity on success.
            None if token invalid, env vars missing, or no identity mapping.
        """
        if not self.issuer or not self.audience:
            logger.debug("OIDC verifier skipped: issuer/audience not configured")
            return None

        payload = verify_oidc_token(
            token, issuer=self.issuer, audience=self.audience
        )
        if payload is None:
            return None

        oidc_sub = payload.get("sub")
        if not oidc_sub:
            logger.warning("OIDC token valid but missing 'sub' claim")
            return None

        principal = resolve_principal(
            oidc_sub, issuer=self.issuer, db_path=self.db_path
        )

        if principal is None:
            # No mapping yet — elicitation required.
            # Return a sentinel so middleware can distinguish
            # "invalid token" from "valid token, needs elicitation".
            logger.info("OIDC sub=%s has no Telegram mapping; elicitation needed", oidc_sub)
            return None

        return AccessToken(
            token=token,
            client_id=principal,
            scopes=["read", "write"],
        )
