"""OIDC auth integration for FastMCP server.

Wires OidcTokenVerifier, elicitation tools, and TTL sweep into the server.
Activated only when TG_OIDC_ISSUER and TG_OIDC_AUDIENCE are set.
"""

import logging
import os

logger = logging.getLogger(__name__)


def oidc_enabled() -> bool:
    """Check if OIDC auth is configured via environment variables."""
    return bool(os.environ.get("TG_OIDC_ISSUER") and os.environ.get("TG_OIDC_AUDIENCE"))


def create_oidc_verifier(db_path: str | None = None):
    """Create OidcTokenVerifier instance from env vars.

    Returns None if OIDC is not configured.
    """
    if not oidc_enabled():
        return None

    from src.auth.oauth_provider_adapter import OidcTokenVerifier

    issuer = os.environ["TG_OIDC_ISSUER"]
    audience = os.environ["TG_OIDC_AUDIENCE"]
    logger.info("OIDC auth enabled: issuer=%s audience=%s", issuer, audience)
    return OidcTokenVerifier(
        issuer=issuer,
        audience=audience,
        db_path=db_path or os.environ.get("TG_DATABASE_URL"),
    )


def register_elicitation_tools(mcp) -> None:
    """Register OIDC elicitation tools on the FastMCP server."""
    from src.auth.elicitation_tools import (
        oidc_setup_code,
        oidc_setup_password,
        oidc_setup_phone,
        oidc_setup_start,
    )

    mcp.tool()(oidc_setup_start)
    mcp.tool()(oidc_setup_phone)
    mcp.tool()(oidc_setup_code)
    mcp.tool()(oidc_setup_password)
    logger.info("Registered 4 OIDC elicitation tools")


async def ttl_sweep_task(
    interval_seconds: int = 60, max_age_seconds: int = 300
) -> None:
    """Background task: clean up expired elicitation states every interval."""
    import asyncio

    from src.auth.queries.setup_state import delete_expired

    logger.info(
        "Starting OIDC elicitation TTL sweep (interval=%ds, max_age=%ds)",
        interval_seconds,
        max_age_seconds,
    )
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            if deleted := delete_expired(max_age_seconds):
                logger.info(
                    "TTL sweep: cleaned %d expired elicitation session(s)", deleted
                )
        except asyncio.CancelledError:
            logger.info("TTL sweep task cancelled")
            break
        except Exception as e:
            logger.error("TTL sweep error: %s", e)
