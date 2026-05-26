"""
Opt-in per-token session ACL for http-auth deployments.

When ACL is enabled, tokens listed in the config file get chat and operation
restrictions. Tokens not listed keep full account access (backward compatible).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any, Callable

import yaml

from src.client.connection import get_request_token
from src.config.server_config import get_config
from src.utils.error_handling import log_and_build_error

logger = logging.getLogger(__name__)

_WRITE_OPERATIONS = frozenset(
    {
        "send_message",
        "edit_message",
        "send_message_to_phone",
    }
)
_READ_OPERATIONS = frozenset(
    {
        "get_messages",
        "get_chat_info",
        "find_chats",
        "search_messages_globally",
    }
)

_acl_cache: dict[str, Any] | None = None
_acl_cache_path: Path | None = None


@dataclass(frozen=True)
class TokenAclRule:
    chats: frozenset[str | int] = field(default_factory=frozenset)
    read_only: bool = False
    allow_global_search: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenAclRule:
        raw_chats = data.get("chats") or []
        chats: set[str | int] = set()
        for item in raw_chats:
            chats.add(_normalize_chat_ref(item))
        return cls(
            chats=frozenset(chats),
            read_only=bool(data.get("read_only", False)),
            allow_global_search=bool(data.get("allow_global_search", True)),
        )


def clear_acl_cache() -> None:
    """Reset loaded ACL config (for tests)."""
    global _acl_cache, _acl_cache_path
    _acl_cache = None
    _acl_cache_path = None


def _acl_file_path() -> Path | None:
    config = get_config()
    if not config.acl_enabled:
        return None
    path = config.acl_config_file
    return path if path.is_file() else None


def _load_acl_document() -> dict[str, Any]:
    global _acl_cache, _acl_cache_path
    path = _acl_file_path()
    if path is None:
        _acl_cache = {}
        _acl_cache_path = None
        return _acl_cache

    if _acl_cache is not None and _acl_cache_path == path:
        return _acl_cache

    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        doc = json.loads(text)
    else:
        doc = yaml.safe_load(text) or {}

    if not isinstance(doc, dict):
        raise ValueError(f"ACL config must be a mapping: {path}")

    _acl_cache = doc
    _acl_cache_path = path
    logger.info("Loaded session ACL config from %s", path)
    return _acl_cache


def _rules_for_token(token: str | None) -> TokenAclRule | None:
    if not token:
        return None
    config = get_config()
    if not config.acl_enabled or config.disable_auth:
        return None

    doc = _load_acl_document()
    tokens = doc.get("tokens") or {}
    if not isinstance(tokens, dict):
        return None

    raw = tokens.get(token)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        logger.warning("Ignoring invalid ACL entry for token prefix %s...", token[:8])
        return None
    return TokenAclRule.from_dict(raw)


def _normalize_chat_ref(value: Any) -> str | int:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in {"me", "saved", "saved messages"}:
        return "me"
    if text.startswith("@"):
        return text[1:].lower()
    try:
        return int(text)
    except ValueError:
        return lowered


def _chat_ref_matches(allowed: str | int, candidate: str | int) -> bool:
    if allowed == candidate:
        return True
    if isinstance(allowed, str) and isinstance(candidate, str):
        return allowed.lower() == candidate.lower()
    return False


def _is_chat_allowed(chat_ref: Any, rule: TokenAclRule) -> bool:
    if not rule.chats:
        return False
    normalized = _normalize_chat_ref(chat_ref)
    return any(_chat_ref_matches(a, normalized) for a in rule.chats)


def _deny(operation: str, message: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return log_and_build_error(
        operation=operation,
        error_message=message,
        params=params,
        error_code=-32007,
    )


def check_pre_tool_access(operation_name: str, kwargs: dict[str, Any]) -> dict[str, Any] | None:
    """Return an error dict when the tool call must be blocked, else None."""
    rule = _rules_for_token(get_request_token())
    if rule is None:
        return None

    if rule.read_only and operation_name in _WRITE_OPERATIONS:
        return _deny(
            operation_name,
            "Session ACL is read-only: send and edit operations are not permitted.",
            params=kwargs,
        )

    if operation_name == "invoke_mtproto" and rule.read_only:
        return _deny(
            operation_name,
            "Session ACL is read-only: invoke_mtproto is not permitted.",
            params=kwargs,
        )

    if operation_name == "search_messages_globally" and not rule.allow_global_search:
        return _deny(
            operation_name,
            "Session ACL disallows global message search for this token.",
            params=kwargs,
        )

    chat_id = kwargs.get("chat_id")
    if chat_id is not None and operation_name in _WRITE_OPERATIONS | {"get_messages", "get_chat_info"}:
        if not _is_chat_allowed(chat_id, rule):
            return _deny(
                operation_name,
                "Session ACL: chat is not in the allowed list for this token.",
                params={"chat_id": chat_id},
            )

    if operation_name == "send_message_to_phone" and rule.chats:
        return _deny(
            operation_name,
            "Session ACL: send_message_to_phone is blocked when a chat whitelist is configured.",
            params=kwargs,
        )

    return None


def _message_chat_id(message: dict[str, Any]) -> str | int | None:
    for key in ("chat_id", "peer_id"):
        if key in message:
            return _normalize_chat_ref(message[key])
    chat = message.get("chat")
    if isinstance(chat, dict) and "id" in chat:
        return _normalize_chat_ref(chat["id"])
    return None


def filter_tool_result(operation_name: str, result: Any) -> Any:
    """Post-filter tool results to enforce chat whitelist on list payloads."""
    rule = _rules_for_token(get_request_token())
    if rule is None or not rule.chats or not isinstance(result, dict):
        return result

    if operation_name == "find_chats" and isinstance(result.get("chats"), list):
        filtered = [
            chat
            for chat in result["chats"]
            if isinstance(chat, dict)
            and _is_chat_allowed(chat.get("id") or chat.get("chat_id"), rule)
        ]
        return {**result, "chats": filtered}

    if operation_name == "search_messages_globally" and isinstance(result.get("messages"), list):
        filtered = [
            msg
            for msg in result["messages"]
            if isinstance(msg, dict)
            and (cid := _message_chat_id(msg)) is not None
            and _is_chat_allowed(cid, rule)
        ]
        return {**result, "messages": filtered}

    if operation_name == "get_messages" and isinstance(result.get("messages"), list):
        chat_id = result.get("chat_id")
        if chat_id is not None and not _is_chat_allowed(chat_id, rule):
            return _deny(
                operation_name,
                "Session ACL: chat is not in the allowed list for this token.",
                params={"chat_id": chat_id},
            )

    return result


def enforce_session_acl(operation_name: str):
    """Decorator: pre-check ACL and post-filter list results."""

    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            denial = check_pre_tool_access(operation_name, kwargs)
            if denial is not None:
                return denial
            result = await func(*args, **kwargs)
            if isinstance(result, dict) and result.get("ok") is False:
                return result
            return filter_tool_result(operation_name, result)

        return wrapper

    return decorator


def check_mtproto_api_access(
    token: str | None, *, allow_dangerous: bool
) -> dict[str, Any] | None:
    """ACL gate for HTTP MTProto bridge (http-auth only)."""
    rule = _rules_for_token(token)
    if rule is None:
        return None
    if rule.read_only:
        return _deny(
            "mtproto_api",
            "Session ACL is read-only: HTTP MTProto bridge is not permitted.",
            params={"allow_dangerous": allow_dangerous},
        )
    return None
