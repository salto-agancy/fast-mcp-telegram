"""Telegram 64-bit id serialization helpers.

Telegram identifiers (user/chat/channel ids, ``access_hash``, ``document_id``,
message ids, etc.) are 64-bit integers. When emitted as JSON *numbers* they are
parsed by JS/double-based consumers (Claude Web, browsers) through IEEE-754
doubles, which only hold 53 bits of integer precision. Any id above 2**53 loses
its low digits: e.g. the custom-emoji ``document_id`` 5366182128746793135 is
read back as 5366182128746793000.

To keep round-trips lossless, 64-bit ids must leave the server as JSON
*strings*. Two layers use this module:

* Structured tools (``find_chats``, ``get_chat_info``, ``get_messages``,
  ``search_messages_globally``) stringify a fixed set of documented id keys
  *always* — so a field's type is stable regardless of the concrete value.
* ``invoke_mtproto`` returns arbitrary nested TL ``to_dict()`` trees where keys
  cannot be enumerated; there we stringify by magnitude (only ints outside the
  JS-safe range), leaving small ints like offsets/lengths/flags as numbers.
"""

from __future__ import annotations

from typing import Any

# Largest integer a double-precision float can represent exactly: 2**53 - 1.
JS_SAFE_MAX = 2**53 - 1

# Documented 64-bit id keys emitted by structured tools. These are stringified
# unconditionally (even when small) so the wire type never silently switches
# between number and string across responses.
INT64_ID_KEYS = frozenset(
    {
        "id",
        "access_hash",
        "document_id",
        "message_id",
        "reply_to_msg_id",
        "topic_id",
        "user_id",
        "chat_id",
        "channel_id",
        "sender_id",
        "from_id",
        "peer_id",
        "completed_by",
    }
)


def stringify_int64(value: Any) -> Any:
    """Return ``str(value)`` when *value* is an int outside the JS-safe range.

    ``bool`` is an ``int`` subclass but is left untouched. Non-ints and
    JS-safe-range ints pass through unchanged. Used for magnitude-based
    sanitization of arbitrary nested structures (``invoke_mtproto``).
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return value
    return str(value) if abs(value) > JS_SAFE_MAX else value


def id_to_str(value: Any) -> Any:
    """Stringify a documented id field value, preserving ``None``.

    Unlike :func:`stringify_int64`, this converts *any* int (regardless of
    magnitude) so a given id key always serializes as a string.
    """
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value)
    return value
