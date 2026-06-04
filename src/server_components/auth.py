import contextlib
import logging
from collections.abc import Callable
from functools import wraps

from src.client.connection import set_request_token
from src.config.server_config import get_config
from src.server_components.session_token_validation import (
    RESERVED_SESSION_NAMES,  # noqa: F401 — re-exported for tests
    InvalidSessionTokenError,
    validate_session_token,
)

logger = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """Exception raised when authentication fails."""


def _extract_bearer_token_from_headers(headers: dict[str, str]) -> str | None:
    """
    Extract Bearer token from HTTP headers with validation.

    Args:
        headers: Dictionary of HTTP headers

    Returns:
        Bearer token string if valid, None otherwise
    """
    auth_header = headers.get("authorization", "")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header[7:].strip()
    if not token:
        return None

    try:
        return validate_session_token(token)
    except InvalidSessionTokenError:
        return None


def extract_bearer_token() -> str | None:
    """
    Extract Bearer token from HTTP Authorization header if running over HTTP.
    Returns None for non-HTTP transports or when header is missing/invalid.
    Validates that token is not a reserved session name to prevent session conflicts.
    """
    try:
        config = get_config()
        if config.transport != "http":
            return None

        # Imported lazily to avoid dependency during stdio runs
        from fastmcp.server.dependencies import get_http_headers

        headers = get_http_headers()
        return _extract_bearer_token_from_headers(headers)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"Error extracting bearer token: {e}")
        return None


def with_auth_context(func: Callable) -> Callable:
    """Decorator to set Bearer token in request context for get_client_by_token.

    When auth is disabled (stdio, http-no-auth): uses default session (None).
    When auth is required (http-auth): uses get_access_token() from FastMCP's
    auth middleware. The token comes from SessionFileTokenVerifier which runs
    before the Mount, bypassing the get_http_headers bug (FastMCP #596).
    """

    @wraps(func)
    async def wrapper(*args, **kwargs):
        config = get_config()

        if config.disable_auth:
            set_request_token(None)
            return await func(*args, **kwargs)

        # http-auth mode: token comes from FastMCP auth provider (SessionFileTokenVerifier)
        from fastmcp.server.dependencies import get_access_token

        access_token = get_access_token()

        if access_token is None:
            error_msg = (
                "Missing Bearer token in Authorization header. HTTP requests require "
                "authentication. Use: 'Authorization: Bearer <your-token>' header."
            )
            logger.warning(f"Authentication failed: {error_msg}")
            raise AuthenticationError(error_msg)

        session_id = access_token.token
        set_request_token(session_id)
        logger.info(f"Bearer token from auth provider for request: {session_id[:8]}...")

        return await func(*args, **kwargs)

    return wrapper


def extract_bearer_token_from_request(request) -> str | None:
    """
    Extract Bearer token from an incoming Starlette request when running over HTTP.

    Behavior:
    - Reads Authorization header directly from the request (custom route safe)
    - Falls back to FastMCP's get_http_headers helper when available
    - Returns None in non-HTTP transports or when header is missing/invalid
    - Validates that token is not a reserved session name to prevent session conflicts
    """
    try:
        config = get_config()
        if config.transport != "http":
            return None

        # Prefer direct read from the incoming request (custom routes)
        with contextlib.suppress(Exception):
            headers = dict(request.headers)
            if token := _extract_bearer_token_from_headers(headers):
                return token
        # Fallback: FastMCP dependency (works in tool-execution context)
        try:  # pragma: no cover - optional path
            from fastmcp.server.dependencies import get_http_headers

            headers = get_http_headers()
            return _extract_bearer_token_from_headers(headers)
        except Exception:
            return None
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"Error extracting bearer token from request: {e}")
        return None
