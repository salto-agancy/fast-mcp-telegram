"""Dialog last-activity bounds and history fallback for find_chats."""

from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from src.utils.datetime_parse import parse_iso_datetime_utc
from src.utils.error_handling import log_and_build_error


def _last_activity_datetime_in_range(
    activity: datetime,
    min_date_dt: datetime | None,
    max_date_dt: datetime | None,
) -> bool:
    """Same window semantics as the truthy path in _dialog_in_date_range (inclusive min, max upper bound)."""
    if activity.tzinfo is None:
        activity = activity.replace(tzinfo=UTC)
    if max_date_dt and activity > max_date_dt:
        return False
    return not (min_date_dt and activity < min_date_dt)


def _validate_find_chats_min_max_dates(
    min_date: str | None, max_date: str | None
) -> tuple[dict[str, Any] | None, datetime | None, datetime | None]:
    """Return (error_response, min_date_dt, max_date_dt). Caller returns error_response if set."""
    min_date_dt = parse_iso_datetime_utc(min_date)
    if min_date is not None and min_date_dt is None:
        err = log_and_build_error(
            operation="find_chats",
            error_message=(
                f"Invalid min_date format: '{min_date}'. "
                "Use ISO format (e.g., '2024-01-01')"
            ),
            params={},
            exception=ValueError(f"Invalid min_date format: '{min_date}'"),
        )
        return err, None, None

    max_date_dt = parse_iso_datetime_utc(max_date)
    if max_date is not None and max_date_dt is None:
        err = log_and_build_error(
            operation="find_chats",
            error_message=(
                f"Invalid max_date format: '{max_date}'. "
                "Use ISO format (e.g., '2024-12-31')"
            ),
            params={},
            exception=ValueError(f"Invalid max_date format: '{max_date}'"),
        )
        return err, None, None

    return None, min_date_dt, max_date_dt


def _dialog_top_date_outside_find_chats_bounds(
    dialog_date,
    min_date_dt: datetime | None,
    max_date_dt: datetime | None,
) -> bool:
    """True if dialog top date is strictly outside the inclusive window."""
    if dialog_date is None or (min_date_dt is None and max_date_dt is None):
        return False
    ddt = dialog_date
    if ddt.tzinfo is None:
        ddt = ddt.replace(tzinfo=UTC)
    if max_date_dt and ddt > max_date_dt:
        return True
    return bool(min_date_dt and ddt < min_date_dt)


async def _filter_flags_entity_allowed_by_date_bounds(
    entity,
    client,
    dialog_date,
    min_date_dt: datetime | None,
    max_date_dt: datetime | None,
) -> bool:
    """
    False when date filters are active, dialog has no top date, and history fallback is out of range.
    """
    if min_date_dt is None and max_date_dt is None:
        return True
    if dialog_date is not None:
        return True
    return await _dialog_in_date_range(entity, client, None, min_date_dt, max_date_dt)


async def _get_last_message_date(entity, client) -> str | None:
    """Get last message date from chat history as fallback when dialog.date is unavailable."""
    with suppress(Exception):
        async for msg in client.iter_messages(entity, limit=1):
            if msg and msg.date:
                return msg.date.isoformat()
    return None


async def _dialog_in_date_range(
    entity,
    client,
    dialog_date,
    min_date_dt: datetime | None,
    max_date_dt: datetime | None,
) -> bool:
    """
    Check if dialog is in date range.

    Returns True if dialog should be included, False otherwise.
    """
    if dialog_date:
        if dialog_date.tzinfo is None:
            dialog_date = dialog_date.replace(tzinfo=UTC)

        if max_date_dt and dialog_date > max_date_dt:
            return False
        return not min_date_dt or dialog_date >= min_date_dt

    if min_date_dt is None and max_date_dt is None:
        return True

    fallback_date = await _get_last_message_date(entity, client)
    if not fallback_date:
        return False

    return (
        (not min_date_dt or fallback_dt >= min_date_dt)
        and (not max_date_dt or fallback_dt <= max_date_dt)
        if (fallback_dt := parse_iso_datetime_utc(fallback_date))
        else False
    )
