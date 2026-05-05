"""Telegram dialog filter resolution and flag matching."""

from datetime import UTC, datetime

from telethon.tl.types import Channel as TelethonChannel
from telethon.tl.types import Chat as TelethonChat
from telethon.tl.types import User as TelethonUser

from src.utils.entity import get_dialog_filters
from src.utils.helpers import normalize_whitespace_lower


async def _get_filter_by_name(client, filter_name: str) -> dict | None:
    """Find filter by name (string). Returns full filter dict or None."""
    filters = await get_dialog_filters(client)
    normalized = normalize_whitespace_lower(filter_name)
    return next(
        (
            f
            for f in filters
            if normalize_whitespace_lower(f.get("title", "")) == normalized
        ),
        None,
    )


def _filter_matches_flags(entity, dialog, filter_dict: dict) -> bool:
    """Check if entity matches filter flags.

    filter_dict contains: contacts, non_contacts, groups, broadcasts, bots,
    exclude_muted, exclude_read, exclude_archived (from filter's flags)

    Note: exclude_muted/exclude_read/exclude_archived require dialog object,
    not just entity. entity param is the Chat/User/Channel, dialog is the Dialog object.
    """
    contacts_flag = filter_dict.get("contacts", False)
    non_contacts_flag = filter_dict.get("non_contacts", False)
    groups_flag = filter_dict.get("groups", False)
    broadcasts_flag = filter_dict.get("broadcasts", False)
    bots_flag = filter_dict.get("bots", False)

    if (
        groups_flag
        or broadcasts_flag
        or bots_flag
        or contacts_flag
        or non_contacts_flag
    ):
        passes = False

        is_chat = isinstance(entity, TelethonChat)
        is_channel = isinstance(entity, TelethonChannel)
        if groups_flag and (
            is_chat or (is_channel and getattr(entity, "megagroup", False))
        ):
            passes = True
        if broadcasts_flag and is_channel and getattr(entity, "broadcast", False):
            passes = True
        is_user = isinstance(entity, TelethonUser)

        if bots_flag and is_user and getattr(entity, "bot", False):
            passes = True
        if (
            contacts_flag
            and is_user
            and (
                getattr(entity, "contact", False)
                or getattr(entity, "mutual_contact", False)
            )
        ):
            passes = True
        if (
            non_contacts_flag
            and is_user
            and not getattr(entity, "contact", False)
            and not getattr(entity, "mutual_contact", False)
        ):
            passes = True

        if not passes:
            return False

    ns = getattr(getattr(dialog, "dialog", None), "notify_settings", None)
    mute_until = getattr(ns, "mute_until", None) if ns else None
    if (
        filter_dict.get("exclude_muted")
        and mute_until
        and mute_until > datetime.now(UTC)
    ):
        return False
    return (
        False
        if filter_dict.get("exclude_read") and getattr(dialog, "unread_count", 0) == 0
        else not filter_dict.get("exclude_archived")
        or getattr(dialog, "folder_id", None) != 1
    )
