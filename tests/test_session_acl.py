"""Tests for opt-in session ACL enforcement."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.client.connection import set_request_token
from src.config.server_config import ServerConfig, ServerMode, set_config
from src.server_components.session_acl import (
    TokenAclRule,
    check_mtproto_api_access,
    check_pre_tool_access,
    clear_acl_cache,
    enforce_session_acl,
    filter_tool_result,
    validate_acl_config,
    AclConfigError,
)
from src.server_components.attachment_tickets import (
    clear_attachment_tickets_for_tests,
    get_attachment_ticket,
    mint_attachment_ticket,
)


@pytest.fixture(autouse=True)
def _reset_acl():
    """Clear ACL cache and request token between tests (token also reset in conftest)."""
    clear_acl_cache()
    set_request_token(None)
    yield
    clear_acl_cache()
    set_request_token(None)


@pytest.fixture
def acl_config(tmp_path: Path):
    acl_file = tmp_path / "acl.yaml"
    acl_file.write_text(
        """
tokens:
  token-readonly:
    chats:
      - me
    read_only: true
    allow_global_search: true
  token-team:
    chats:
      - -1001234567890
      - "@workgroup"
    read_only: false
    allow_global_search: false
  token-auto:
    chats:
      - -100999
    read_only: false
    allow_global_search: false
""",
        encoding="utf-8",
    )
    config = ServerConfig(_cli_parse_args=[])
    config.server_mode = ServerMode.HTTP_AUTH
    config.acl_enabled = True
    config.acl_config_path = str(acl_file)
    set_config(config)
    return acl_file


def test_token_without_acl_entry_allows_all(acl_config):
    set_request_token("unlisted-token")
    assert check_pre_tool_access("send_message", {"chat_id": -1}) is None


def test_read_only_blocks_send(acl_config):
    set_request_token("token-readonly")
    denial = check_pre_tool_access("send_message", {"chat_id": "me"})
    assert denial is not None
    assert denial["ok"] is False
    assert "read-only" in denial["error"].lower()


def test_chat_whitelist_blocks_unknown_chat(acl_config):
    set_request_token("token-team")
    denial = check_pre_tool_access("get_messages", {"chat_id": -1000000})
    assert denial is not None
    assert "not in the allowed list" in denial["error"]


def test_chat_whitelist_allows_listed_chat(acl_config):
    set_request_token("token-team")
    assert check_pre_tool_access("get_messages", {"chat_id": -1001234567890}) is None


def test_global_search_blocked_for_automation_persona(acl_config):
    set_request_token("token-auto")
    denial = check_pre_tool_access("search_messages_globally", {"query": "hello"})
    assert denial is not None
    assert "global message search" in denial["error"].lower()


def test_find_chats_post_filter(acl_config):
    set_request_token("token-team")
    result = {
        "chats": [
            {"id": -1001234567890, "title": "Work"},
            {"id": -1000000, "title": "Other"},
        ]
    }
    filtered = filter_tool_result("find_chats", result)
    assert len(filtered["chats"]) == 1
    assert filtered["chats"][0]["id"] == -1001234567890


def test_acl_disabled_skips_rules(tmp_path):
    acl_file = tmp_path / "acl.yaml"
    acl_file.write_text('tokens:\n  t:\n    read_only: true\n', encoding="utf-8")
    config = ServerConfig(_cli_parse_args=[])
    config.server_mode = ServerMode.HTTP_AUTH
    config.acl_enabled = False
    config.acl_config_path = str(acl_file)
    set_config(config)
    set_request_token("t")
    assert check_pre_tool_access("send_message", {"chat_id": "me"}) is None


def test_json_acl_file(tmp_path):
    acl_file = tmp_path / "acl.json"
    acl_file.write_text(
        json.dumps(
            {
                "tokens": {
                    "json-token": {
                        "chats": ["me"],
                        "read_only": True,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    config = ServerConfig(_cli_parse_args=[])
    config.server_mode = ServerMode.HTTP_AUTH
    config.acl_enabled = True
    config.acl_config_path = str(acl_file)
    set_config(config)
    set_request_token("json-token")
    assert check_pre_tool_access("edit_message", {"chat_id": "me"}) is not None


def test_token_acl_rule_from_dict():
    rule = TokenAclRule.from_dict(
        {"chats": ["@Team", "me", -1001], "read_only": True, "allow_global_search": False}
    )
    assert rule.read_only is True
    assert rule.allow_global_search is False
    assert "team" in rule.chats
    assert "me" in rule.chats
    assert -1001 in rule.chats


def test_invoke_mtproto_blocked_when_chat_whitelist_configured(acl_config):
    set_request_token("token-team")
    denial = check_pre_tool_access(
        "invoke_mtproto",
        {"method_full_name": "messages.GetHistory", "params_json": "{}"},
    )
    assert denial is not None
    assert "listed in the acl config" in denial["error"].lower()


def test_invoke_mtproto_read_only_blocks_even_without_chat_whitelist(tmp_path):
    acl_file = tmp_path / "acl.yaml"
    acl_file.write_text(
        'tokens:\n  ro-no-chats:\n    chats:\n      - me\n    read_only: true\n',
        encoding="utf-8",
    )
    config = ServerConfig(_cli_parse_args=[])
    config.server_mode = ServerMode.HTTP_AUTH
    config.acl_enabled = True
    config.acl_config_path = str(acl_file)
    set_config(config)
    set_request_token("ro-no-chats")
    denial = check_pre_tool_access("invoke_mtproto", {"params_json": "{}"})
    assert denial is not None
    assert "read-only" in denial["error"].lower()


def test_invoke_mtproto_blocked_when_empty_lane(tmp_path):
    acl_file = tmp_path / "acl.yaml"
    acl_file.write_text(
        'tokens:\n  open-empty:\n    chats: []\n    read_only: false\n',
        encoding="utf-8",
    )
    config = ServerConfig(_cli_parse_args=[])
    config.server_mode = ServerMode.HTTP_AUTH
    config.acl_enabled = True
    config.acl_config_path = str(acl_file)
    set_config(config)
    set_request_token("open-empty")
    denial = check_pre_tool_access("invoke_mtproto", {"params_json": "{}"})
    assert denial is not None
    assert "listed in the acl config" in denial["error"].lower()


def test_mtproto_api_blocked_when_chat_whitelist_configured(acl_config):
    denial = check_mtproto_api_access("token-team", allow_dangerous=False)
    assert denial is not None
    assert "listed in the acl config" in denial["error"].lower()


def test_acl_enabled_missing_file_raises(tmp_path):
    missing = tmp_path / "missing-acl.yaml"
    config = ServerConfig(_cli_parse_args=[])
    config.server_mode = ServerMode.HTTP_AUTH
    config.acl_enabled = True
    config.acl_config_path = str(missing)
    set_config(config)
    with pytest.raises(AclConfigError, match="ACL is enabled"):
        validate_acl_config()


def test_validate_config_raises_when_acl_file_missing(tmp_path):
    missing = tmp_path / "missing-acl.yaml"
    config = ServerConfig(_cli_parse_args=[])
    config.server_mode = ServerMode.HTTP_AUTH
    config.acl_enabled = True
    config.acl_config_path = str(missing)
    with pytest.raises(AclConfigError, match="ACL is enabled"):
        config.validate_config()


@pytest.mark.asyncio
async def test_get_messages_post_filter_revokes_minted_attachment_tickets(acl_config):
    """Post-filter ACL denial must revoke tickets minted during the tool call."""
    await clear_attachment_tickets_for_tests()
    set_request_token("token-team")
    minted_ticket_id: str | None = None

    @enforce_session_acl("get_messages")
    async def get_messages(chat_id):
        nonlocal minted_ticket_id
        minted_ticket_id = await mint_attachment_ticket("token-team", -1000000, 42)
        return {
            "ok": True,
            "chat_id": -1000000,
            "messages": [{"id": 42}],
        }

    result = await get_messages(chat_id=-1001234567890)

    assert result["ok"] is False
    assert "not in the allowed list" in result["error"]
    assert minted_ticket_id is not None
    assert await get_attachment_ticket(minted_ticket_id) is None
    await clear_attachment_tickets_for_tests()


@pytest.fixture
def empty_lane_config(tmp_path: Path):
    acl_file = tmp_path / "acl.yaml"
    acl_file.write_text(
        'tokens:\n  empty-lane:\n    chats: []\n    read_only: false\n',
        encoding="utf-8",
    )
    config = ServerConfig(_cli_parse_args=[])
    config.server_mode = ServerMode.HTTP_AUTH
    config.acl_enabled = True
    config.acl_config_path = str(acl_file)
    set_config(config)
    return acl_file


def test_empty_lane_blocks_find_chats_pre_check(empty_lane_config):
    set_request_token("empty-lane")
    denial = check_pre_tool_access("find_chats", {"query": "work"})
    assert denial is not None
    assert denial["ok"] is False
    assert "empty chat lane" in denial["error"].lower()


def test_empty_lane_blocks_search_messages_globally(empty_lane_config):
    set_request_token("empty-lane")
    denial = check_pre_tool_access("search_messages_globally", {"query": "secret"})
    assert denial is not None
    assert "empty chat lane" in denial["error"].lower()


def test_empty_lane_blocks_get_messages(empty_lane_config):
    set_request_token("empty-lane")
    denial = check_pre_tool_access("get_messages", {"chat_id": -100123})
    assert denial is not None
    assert "not in the allowed list" in denial["error"]


def test_empty_lane_find_chats_post_filter_hard_deny(empty_lane_config):
    """Empty lane must hard-deny find_chats, not return an empty list (leak prevention)."""
    set_request_token("empty-lane")
    result = filter_tool_result(
        "find_chats",
        {"ok": True, "chats": [{"id": -1001234567890, "title": "Leaked"}]},
    )
    assert result["ok"] is False
    assert "empty chat lane" in result["error"].lower()


def test_validate_acl_rejects_read_only_without_chats(tmp_path):
    acl_file = tmp_path / "acl.yaml"
    acl_file.write_text(
        'tokens:\n  bad-analyst:\n    read_only: true\n',
        encoding="utf-8",
    )
    config = ServerConfig(_cli_parse_args=[])
    config.server_mode = ServerMode.HTTP_AUTH
    config.acl_enabled = True
    config.acl_config_path = str(acl_file)
    set_config(config)
    with pytest.raises(AclConfigError, match="read_only: true but empty"):
        validate_acl_config()


def test_validate_acl_rejects_malformed_yaml(tmp_path):
    acl_file = tmp_path / "acl.yaml"
    acl_file.write_text("tokens:\n  bad:\n    chats: [\n", encoding="utf-8")
    config = ServerConfig(_cli_parse_args=[])
    config.server_mode = ServerMode.HTTP_AUTH
    config.acl_enabled = True
    config.acl_config_path = str(acl_file)
    set_config(config)
    with pytest.raises(AclConfigError, match="not valid YAML"):
        validate_acl_config()


def test_validate_acl_rejects_malformed_json(tmp_path):
    acl_file = tmp_path / "acl.json"
    acl_file.write_text('{"tokens": ', encoding="utf-8")
    config = ServerConfig(_cli_parse_args=[])
    config.server_mode = ServerMode.HTTP_AUTH
    config.acl_enabled = True
    config.acl_config_path = str(acl_file)
    set_config(config)
    with pytest.raises(AclConfigError, match="not valid JSON"):
        validate_acl_config()


def test_validate_acl_rejects_malformed_token_entry(tmp_path):
    acl_file = tmp_path / "acl.yaml"
    acl_file.write_text(
        'tokens:\n  bad-token: "not-a-mapping"\n',
        encoding="utf-8",
    )
    config = ServerConfig(_cli_parse_args=[])
    config.server_mode = ServerMode.HTTP_AUTH
    config.acl_enabled = True
    config.acl_config_path = str(acl_file)
    set_config(config)
    with pytest.raises(AclConfigError, match="must be a mapping"):
        validate_acl_config()
