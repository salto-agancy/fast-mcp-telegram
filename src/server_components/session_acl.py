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

from src.config.server_config import get_config
from src.server_components.attachment_tickets import (
    revoke_attachment_tickets,
    track_minted_attachment_tickets,
)
from src.utils.error_handling import log_and_build_error

logger = logging.getLogger(__name__)

_WRITE_OPERATIONS = frozenset(
    {
        "send_message",
        "edit_message",
        "send_message_to_phone",
    }
)
_LIST_RESULT_OPERATIONS = frozenset({"find_chats", "search_messages_globally"})

_EMPTY_LANE_DENY_MSG = (
    "Session ACL: this token has an empty chat lane (chats: [] or chats omitted). "
    "Add at least one chat id, @username, or me to the token entry in the ACL config."
)
_LISTED_TOKEN_MTPROTO_DENY_MSG = (
    "Session ACL: invoke_mtproto is not permitted for tokens listed in the ACL config."
)
_LISTED_TOKEN_MTPROTO_API_DENY_MSG = (
    "Session ACL: HTTP MTProto bridge is not permitted for tokens listed in the ACL config."
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


def _configured_acl_path() -> Path | None:
    config = get_config()
    if not config.acl_enabled:
        return None
    return config.acl_config_file


class AclConfigError(ValueError):
    """Raised when ACL is enabled but the config file is missing or invalid."""


def _validate_acl_document(doc: dict[str, Any], path: Path) -> None:
    """Fail-closed startup validation for token entries."""
    tokens = doc.get("tokens") or {}
    if not isinstance(tokens, dict):
        raise AclConfigError(f"ACL config 'tokens' must be a mapping: {path}")

    for token_key, raw in tokens.items():
        prefix = str(token_key)[:8]
        if not isinstance(raw, dict):
            raise AclConfigError(
                f"ACL config entry for token prefix {prefix}... must be a mapping, "
                f"not {type(raw).__name__}: {path}"
            )
        rule = TokenAclRule.from_dict(raw)
        if rule.read_only and not rule.chats:
            raise AclConfigError(
                f"ACL token prefix {prefix}... has read_only: true but empty or missing "
                f"chats. Analyst profile requires a non-empty chat lane: {path}"
            )


def validate_acl_config() -> None:
    """Fail-closed: refuse startup when ACL is enabled but config is absent or invalid."""
    config = get_config()
    if not config.acl_enabled or config.disable_auth:
        return
    path = config.acl_config_file
    if not path.is_file():
        raise AclConfigError(
            f"ACL is enabled (ACL_ENABLED=true) but ACL config file not found: {path}. "
            "Create the file or set ACL_CONFIG_PATH to a valid path."
        )
    _load_acl_document()


def _load_acl_document() -> dict[str, Any]:
    global _acl_cache, _acl_cache_path
    path = _configured_acl_path()
    if path is None:
        _acl_cache = {}
        _acl_cache_path = None
        return _acl_cache

    if not path.is_file():
        raise AclConfigError(f"ACL config file not found: {path}")

    if _acl_cache is not None and _acl_cache_path == path:
        return _acl_cache

    text = path.read_text(encoding="utf-8")
    try:
        if path.suffix.lower() == ".json":
            doc = json.loads(text)
        else:
            doc = yaml.safe_load(text) or {}
    except json.JSONDecodeError as exc:
        raise AclConfigError(
            f"ACL config is not valid JSON: {path} ({exc.msg} at line {exc.lineno}, "
            f"column {exc.colno})"
        ) from exc
    except yaml.YAMLError as exc:
        raise AclConfigError(f"ACL config is not valid YAML: {path} ({exc})") from exc

    if not isinstance(doc, dict):
        raise AclConfigError(f"ACL config must be a mapping: {path}")

    _validate_acl_document(doc, path)

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
        logger.error(
            "Invalid ACL entry for token prefix %s... (expected mapping); denying all tool access",
            token[:8],
        )
        return TokenAclRule()
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


def _is_empty_lane(rule: TokenAclRule) -> bool:
    return not rule.chats


def _is_chat_allowed(chat_ref: Any, rule: TokenAclRule) -> bool:
    if _is_empty_lane(rule):
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
    from src.client.connection import get_request_token

    rule = _rules_for_token(get_request_token())
    if rule is None:
        return None

    if rule.read_only and operation_name in _WRITE_OPERATIONS:
        return _deny(
            operation_name,
            "Session ACL is read-only: send and edit operations are not permitted.",
            params=kwargs,
        )

    if _is_empty_lane(rule):
        if operation_name in _LIST_RESULT_OPERATIONS:
            return _deny(
                operation_name,
                _EMPTY_LANE_DENY_MSG,
                params=kwargs,
            )
        if operation_name == "send_message_to_phone":
            return _deny(
                operation_name,
                _EMPTY_LANE_DENY_MSG,
                params=kwargs,
            )

    if operation_name == "invoke_mtproto":
        if rule.read_only:
            return _deny(
                operation_name,
                "Session ACL is read-only: invoke_mtproto is not permitted.",
                params=kwargs,
            )
        return _deny(
            operation_name,
            _LISTED_TOKEN_MTPROTO_DENY_MSG,
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

    if operation_name == "send_message_to_phone" and not _is_empty_lane(rule):
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
    from src.client.connection import get_request_token

    rule = _rules_for_token(get_request_token())
    if rule is None or not isinstance(result, dict):
        return result

    if _is_empty_lane(rule):
        if operation_name in _LIST_RESULT_OPERATIONS:
            return _deny(operation_name, _EMPTY_LANE_DENY_MSG)
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

            track_tickets = operation_name == "get_messages"
            if track_tickets:
                with track_minted_attachment_tickets() as minted_ids:
                    result = await func(*args, **kwargs)
                    return await _finalize_acl_result(
                        operation_name, result, minted_ids
                    )

            result = await func(*args, **kwargs)
            return await _finalize_acl_result(operation_name, result, [])

        return wrapper

    return decorator


async def _finalize_acl_result(
    operation_name: str,
    result: Any,
    minted_ids: list[str],
) -> Any:
    if isinstance(result, dict) and result.get("ok") is False:
        return result
    was_ok = isinstance(result, dict) and result.get("ok") is not False
    filtered = filter_tool_result(operation_name, result)
    if (
        operation_name == "get_messages"
        and was_ok
        and isinstance(filtered, dict)
        and filtered.get("ok") is False
        and minted_ids
    ):
        removed = await revoke_attachment_tickets(minted_ids)
        if removed:
            logger.info(
                "Revoked %d attachment ticket(s) after get_messages ACL denial",
                removed,
            )
    return filtered


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
    return _deny(
        "mtproto_api",
        _LISTED_TOKEN_MTPROTO_API_DENY_MSG,
        params={"allow_dangerous": allow_dangerous},
    )
