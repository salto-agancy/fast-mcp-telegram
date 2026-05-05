"""Flag-based dialog folder matching over iter_dialogs."""

from typing import Any

from src.utils.entity import build_dialog_entity_dict

from .constants import FLAG_MATCH_MAX_DIALOGS
from .date_helpers import (
    _dialog_top_date_outside_find_chats_bounds,
    _filter_flags_entity_allowed_by_date_bounds,
    _validate_find_chats_min_max_dates,
)
from .dialog_filters import _filter_matches_flags
from .views import (
    ChatView,
    _find_chats_query_matches_entity,
    _match_chat_type,
    _match_public,
)


async def _find_chats_by_filter_flags(
    client,
    filter_dict: dict,
    query: str | None,
    limit: int,
    chat_type: str | None,
    public: bool | None,
    min_date: str | None,
    max_date: str | None,
) -> dict[str, Any]:
    """Handle flag-based filter by iterating all dialogs and matching flags."""
    err, min_date_dt, max_date_dt = _validate_find_chats_min_max_dates(
        min_date, max_date
    )
    if err is not None:
        return err

    results = []
    async for dialog in client.iter_dialogs(
        limit=min(limit * 10, FLAG_MATCH_MAX_DIALOGS)
    ):
        entity = getattr(dialog, "entity", None)
        if not entity:
            continue

        dialog_date = getattr(dialog, "date", None)
        if _dialog_top_date_outside_find_chats_bounds(
            dialog_date, min_date_dt, max_date_dt
        ):
            continue

        if not _filter_matches_flags(entity, dialog, filter_dict):
            continue

        view = ChatView.from_entity(entity)
        if not await _filter_flags_entity_allowed_by_date_bounds(
            entity, client, dialog_date, min_date_dt, max_date_dt
        ):
            continue

        if not _match_chat_type(view, chat_type):
            continue
        if not _match_public(view, public):
            continue

        if result := build_dialog_entity_dict(dialog, entity):
            if not _find_chats_query_matches_entity(view, query):
                continue
            results.append(result)
            if len(results) >= limit:
                break

    return {"chats": results}
