"""Unified chat view model and string matching for client-side filters."""

from dataclasses import dataclass

from src.utils.chat_search_text import chat_searchable_text_lower
from src.utils.entity import build_entity_dict


@dataclass
class ChatView:
    """Unified view of a chat for filtering and display."""

    type: str | None
    username: str | None
    title: str | None
    first_name: str | None
    last_name: str | None
    phone: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "ChatView":
        return cls(
            type=d.get("type"),
            username=d.get("username"),
            title=d.get("title"),
            first_name=d.get("first_name"),
            last_name=d.get("last_name"),
            phone=d.get("phone"),
        )

    @classmethod
    def from_entity(cls, entity) -> "ChatView":
        d = dict(build_entity_dict(entity) or {})
        if ph := getattr(entity, "phone", None):
            d["phone"] = ph
        return cls.from_dict(d)


def _match_chat_type(view: ChatView, chat_type: str | None) -> bool:
    """Check if view matches chat_type filter."""
    if not chat_type:
        return True
    types = [ct.strip().lower() for ct in chat_type.split(",") if ct.strip()]
    valid = {"private", "bot", "group", "channel"}
    if any(ct not in valid for ct in types):
        return False
    return (view.type or "").lower() in types


def _match_public(view: ChatView, public: bool | None) -> bool:
    """Check if view matches public filter."""
    if (view.type or "") in ("private", "bot"):
        return True
    if public is None:
        return True
    has_username = bool(view.username)
    return has_username if public else not has_username


def _match_query(view: ChatView, query_lower: str) -> bool:
    """*query_lower* must already be lowercased."""
    if not query_lower:
        return True
    haystack = chat_searchable_text_lower(
        view.title,
        view.username,
        view.first_name,
        view.last_name,
        view.phone,
    )
    return query_lower in haystack


def _find_chats_query_matches_entity(view: ChatView, query: str | None) -> bool:
    if not query:
        return True
    query_lower = query.lower().strip()
    return not query_lower or _match_query(view, query_lower)
