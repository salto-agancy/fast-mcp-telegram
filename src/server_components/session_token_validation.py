"""Bearer token validation and safe session file path construction."""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Reserved session names that cannot be used as bearer tokens (shared with auth layer).
RESERVED_SESSION_NAMES = frozenset(
    {
        "telegram",
        "default",
        "session",
        "bot",
        "user",
        "main",
        "primary",
        "test",
        "dev",
        "prod",
    }
)

# Matches generate_bearer_token(): 32 random bytes, URL-safe base64 without padding.
BEARER_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")


class InvalidSessionTokenError(ValueError):
    """Bearer token failed format or session-directory containment checks."""


def validate_session_token(token: str) -> str:
    """Return normalized token if valid, else raise InvalidSessionTokenError."""
    if not token or not token.strip():
        raise InvalidSessionTokenError("Empty or whitespace-only session token")

    normalized = token.strip()

    if normalized.lower() in RESERVED_SESSION_NAMES:
        logger.warning(
            "Rejected reserved session name '%s' as bearer token",
            normalized,
        )
        raise InvalidSessionTokenError(
            "Reserved session name is not a valid bearer token"
        )

    if not BEARER_TOKEN_RE.fullmatch(normalized):
        logger.warning(
            "Rejected bearer token with invalid format (length=%s)",
            len(normalized),
        )
        raise InvalidSessionTokenError("Bearer token has invalid format")

    return normalized


def session_file_path(session_dir: Path, token: str) -> Path:
    """Resolve {session_dir}/{token}.session and ensure it stays under session_dir.

    Expects ``token`` to already be validated via ``validate_session_token``.
    """
    base = session_dir.resolve()
    resolved = (base / f"{token}.session").resolve()
    if not resolved.is_relative_to(base):
        raise InvalidSessionTokenError(
            "Session file path escapes configured session directory"
        )
    return resolved


def validated_session_file_path(session_dir: Path, raw_token: str) -> Path:
    """Validate ``raw_token`` and return its confined session file path."""
    return session_file_path(session_dir, validate_session_token(raw_token))
