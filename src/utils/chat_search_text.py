"""Haystack strings for substring matching against Telegram chat/user-like fields."""


def chat_searchable_text_lower(
    title: str | None,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
    phone: str | None,
) -> str:
    """Build a single lowercased searchable string from common entity fields.

    Parts are stripped of falsy values before joining with spaces. Use for
    **haystack** only: callers must pass an already lowercased **needle** when
    using ``needle in haystack`` substring checks.
    """
    parts = (
        (title or "").strip() if title else "",
        (username or "").strip() if username else "",
        (first_name or "").strip() if first_name else "",
        (last_name or "").strip() if last_name else "",
        (phone or "").strip() if phone else "",
    )
    return " ".join(p for p in parts if p).lower()
