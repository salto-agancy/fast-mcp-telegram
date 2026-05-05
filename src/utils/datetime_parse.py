"""Parse ISO 8601 date/time strings for API parameters.

``parse_iso_datetime_utc`` returns ``None`` for missing input or invalid strings.
Callers that accept user-supplied non-empty date strings must treat ``None`` as a
validation failure (see ``find_chats`` / ``get_messages`` error paths).
"""

from contextlib import suppress
from datetime import UTC, datetime


def parse_iso_datetime_utc(raw: str | None) -> datetime | None:
    """Parse an ISO date/time string to a timezone-aware datetime (UTC if naive).

    Returns ``None`` for missing/falsy input or when ``fromisoformat`` rejects the
    string. Does not raise for parse errors.

    **Callers:** If the user provided a non-empty ``min_date`` / ``max_date`` (or
    similar) and this returns ``None``, return a structured validation error; do
    not silently skip date filtering.
    """
    if not raw:
        return None
    with suppress(ValueError):
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    return None
