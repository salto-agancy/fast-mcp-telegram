"""Types and constants for get_messages search package."""

from enum import Enum, auto
from typing import Literal

ThreadScope = Literal["auto", "full", "direct"]

THREAD_SEARCH_CHUNK = 100
FORUM_REPLY_OFFSET_MARGIN_DIRECT = 100
FORUM_REPLY_OFFSET_MARGIN_FULL = 500
FORUM_REPLY_OFFSET_WIDEN = (200, 2000, 20000)
FORUM_ID_WINDOW_MAX_MARGIN = 2000
FORUM_LEGACY_SCAN_CAP = 5000


class MessageRetrievalMode(Enum):
    """Enumeration of message retrieval modes for get_messages."""

    MESSAGE_IDS = auto()
    REPLIES = auto()
    SEARCH = auto()


def resolve_mode(
    *,
    chat_id: str | None,
    query: str | None,
    message_ids: list[int] | None,
    reply_to_id: int | None,
) -> MessageRetrievalMode:
    """
    Determine the message retrieval mode based on parameter combination.

    Raises ValueError if parameters conflict or required parameters are missing.
    """
    if message_ids is not None and reply_to_id is not None:
        raise ValueError(
            "Cannot combine message_ids with reply_to_id. Use one or the other."
        )
    if message_ids is not None and query is not None:
        raise ValueError(
            "Cannot combine message_ids with query. Specific message IDs don't need search."
        )

    if message_ids is not None:
        if not message_ids:
            raise ValueError("message_ids cannot be empty when provided")
        if not chat_id:
            raise ValueError("chat_id is required when using message_ids")
        return MessageRetrievalMode.MESSAGE_IDS

    if reply_to_id is not None:
        if not chat_id:
            raise ValueError("chat_id is required when using reply_to_id")
        return MessageRetrievalMode.REPLIES

    return MessageRetrievalMode.SEARCH
