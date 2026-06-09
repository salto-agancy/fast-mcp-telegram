"""Resolve OIDC sub to Telegram ACL principal string."""
import hashlib
import logging
from typing import Optional

from src.auth.queries.oidc_identity import get_identity

logger = logging.getLogger(__name__)


def _hash_sub(oidc_sub: str, issuer: str) -> str:
    """Produce oidc_key from sub + issuer (matches migration script logic)."""
    return hashlib.sha256(f"{oidc_sub}:{issuer}".encode()).hexdigest()[:32]


def resolve_principal(
    oidc_sub: str,
    issuer: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Optional[str]:
    """Resolve an OIDC sub claim to a Telegram ACL principal string.

    Priority: @username > +phone > str(user_id).
    Returns None if no mapping exists (triggers elicitation in Phase 3).

    Args:
        oidc_sub: The 'sub' claim from the verified JWT.
        issuer: OIDC issuer URL (used to compute oidc_key hash).
                Defaults to TG_OIDC_ISSUER env var.
        db_path: Optional SQLite DB path override.

    Returns:
        ACL-compatible principal string or None.
    """
    import os
    issuer = issuer or os.environ.get("TG_OIDC_ISSUER", "")
    if not issuer:
        logger.warning("resolve_principal: TG_OIDC_ISSUER not set")
        return None

    oidc_key = _hash_sub(oidc_sub, issuer)
    row = get_identity(oidc_key, db_path=db_path)

    if row is None:
        logger.debug("No identity mapping for oidc_sub=%s", oidc_sub)
        return None

    # Priority: username > phone > user_id
    if row["telegram_username"]:
        return f"@{row['telegram_username']}"
    if row["telegram_phone"]:
        return f"+{row['telegram_phone']}"
    if row["telegram_user_id"]:
        return str(row["telegram_user_id"])

    logger.warning("Identity row exists but has no Telegram fields: oidc_key=%s", oidc_key)
    return None
