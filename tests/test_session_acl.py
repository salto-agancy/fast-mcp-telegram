"""Tests for opt-in session ACL enforcement."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.client.connection import set_request_token
from src.config.server_config import ServerConfig, ServerMode, get_config, set_config
from src.server_components.attachment_tickets import (
    clear_attachment_tickets_for_tests,
    get_attachment_ticket,
    mint_attachment_ticket,
)
from src.server_components.session_acl import (
    AclConfigError,
    TokenAclRule,
    check_blocked_peer_mtproto_params,
    check_mtproto_api_access,
    check_pre_tool_access,
    clear_acl_cache,
    enforce_session_acl,
    filter_tool_result,
    merge_mtproto_request_params,
    validate_acl_config,
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
    acl_file.write_text("tokens:\n  t:\n    read_only: true\n", encoding="utf-8")
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
        {
            "chats": ["@Team", "me", -1001],
            "read_only": True,
            "allow_global_search": False,
            "allow_mtproto": True,
        }
    )
    assert rule.read_only is True
    assert rule.allow_global_search is False
    assert rule.allow_mtproto is True
    assert "team" in rule.chats
    assert "me" in rule.chats
    assert -1001 in rule.chats


def test_token_acl_rule_allow_mtproto_defaults_false():
    rule = TokenAclRule.from_dict({"chats": ["me"]})
    assert rule.allow_mtproto is False


def test_invoke_mtproto_blocked_when_chat_whitelist_configured(acl_config):
    set_request_token("token-team")
    denial = check_pre_tool_access(
        "invoke_mtproto",
        {"method_full_name": "messages.GetHistory", "params_json": "{}"},
    )
    assert denial is not None
    assert "allow_global_search" in denial["error"].lower()


def test_invoke_mtproto_read_only_blocks_even_without_chat_whitelist(tmp_path):
    acl_file = tmp_path / "acl.yaml"
    acl_file.write_text(
        "tokens:\n  ro-no-chats:\n    chats:\n      - me\n    read_only: true\n",
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
        "tokens:\n  open-empty:\n    chats: []\n    read_only: false\n",
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
    assert "allow_mtproto" in denial["error"].lower()


def test_mtproto_api_blocked_when_chat_whitelist_configured(acl_config):
    denial = check_mtproto_api_access("token-team", allow_dangerous=False)
    assert denial is not None
    assert "allow_global_search" in denial["error"].lower()


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
        "tokens:\n  empty-lane:\n    chats: []\n    read_only: false\n",
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
        "tokens:\n  bad-analyst:\n    read_only: true\n",
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


# --- Phase 1.5: blocked_peers denylist ---

BOTFATHER_ID = 93372553
LOGIN_SERVICE_ID = 777000


def _write_acl_config(tmp_path: Path, body: str) -> Path:
    acl_file = tmp_path / "acl.yaml"
    acl_file.write_text(body, encoding="utf-8")
    config = ServerConfig(_cli_parse_args=[])
    config.server_mode = ServerMode.HTTP_AUTH
    config.acl_enabled = True
    config.acl_config_path = str(acl_file)
    set_config(config)
    return acl_file


@pytest.fixture
def blocked_peers_base_config(tmp_path: Path):
    return _write_acl_config(
        tmp_path,
        f"""
blocked_peers:
  - {BOTFATHER_ID}
tokens:
  token-team:
    chats:
      - {BOTFATHER_ID}
      - -1001234567890
    read_only: false
""",
    )


def test_blocked_peers_omitted_no_blocking(tmp_path):
    _write_acl_config(
        tmp_path,
        f"""
tokens:
  t:
    chats:
      - {BOTFATHER_ID}
""",
    )
    set_request_token("unlisted-token")
    assert check_pre_tool_access("get_messages", {"chat_id": BOTFATHER_ID}) is None


def test_blocked_peers_numeric_input_pre_check_deny(blocked_peers_base_config):
    set_request_token("unlisted-token")
    denial = check_pre_tool_access("get_messages", {"chat_id": BOTFATHER_ID})
    assert denial is not None
    assert denial["error_code"] == -32007
    assert "blocked peer" in denial["error"]
    assert str(BOTFATHER_ID) in denial["error"]


def test_blocked_peers_numeric_yaml_at_username_input_post_check_deny(
    blocked_peers_base_config,
):
    set_request_token("unlisted-token")
    assert check_pre_tool_access("get_chat_info", {"chat_id": "@BotFather"}) is None
    denial = filter_tool_result(
        "get_chat_info",
        {"ok": True, "id": BOTFATHER_ID, "title": "BotFather"},
    )
    assert denial["ok"] is False
    assert "blocked peer" in denial["error"]


def test_blocked_peers_at_username_yaml_numeric_input_post_check_deny(tmp_path):
    _write_acl_config(
        tmp_path,
        """
blocked_peers:
  - "@BotFather"
tokens: {}
""",
    )
    set_request_token("unlisted-token")
    assert check_pre_tool_access("get_chat_info", {"chat_id": BOTFATHER_ID}) is None
    denial = filter_tool_result(
        "get_chat_info",
        {"ok": True, "id": BOTFATHER_ID, "username": "BotFather"},
    )
    assert denial["ok"] is False
    assert "blocked peer" in denial["error"]


def test_blocked_peers_at_username_input_pre_check_deny(tmp_path):
    _write_acl_config(
        tmp_path,
        """
blocked_peers:
  - "@BotFather"
tokens: {}
""",
    )
    set_request_token("unlisted-token")
    denial = check_pre_tool_access("get_chat_info", {"chat_id": "@BotFather"})
    assert denial is not None
    assert "blocked peer" in denial["error"]


def test_blocked_peers_omitted_id_allowed(blocked_peers_base_config):
    set_request_token("unlisted-token")
    assert check_pre_tool_access("get_messages", {"chat_id": LOGIN_SERVICE_ID}) is None


def test_blocked_peers_deny_wins_over_token_lane(blocked_peers_base_config):
    set_request_token("token-team")
    denial = check_pre_tool_access("get_messages", {"chat_id": BOTFATHER_ID})
    assert denial is not None
    assert "blocked peer" in denial["error"]


def test_blocked_peers_find_chats_post_filter_unlisted_token(blocked_peers_base_config):
    set_request_token("unlisted-token")
    result = filter_tool_result(
        "find_chats",
        {
            "ok": True,
            "chats": [
                {"id": BOTFATHER_ID, "title": "BotFather"},
                {"id": -1001234567890, "title": "Work"},
            ],
        },
    )
    assert len(result["chats"]) == 1
    assert result["chats"][0]["id"] == -1001234567890


def test_blocked_peers_global_search_post_filter_unlisted_token(
    blocked_peers_base_config,
):
    set_request_token("unlisted-token")
    result = filter_tool_result(
        "search_messages_globally",
        {
            "ok": True,
            "messages": [
                {"id": 1, "chat_id": BOTFATHER_ID},
                {"id": 2, "chat_id": -1001234567890},
            ],
        },
    )
    assert len(result["messages"]) == 1
    assert result["messages"][0]["chat_id"] == -1001234567890


def test_blocked_peers_get_chat_info_post_check_id_only(blocked_peers_base_config):
    set_request_token("unlisted-token")
    denial = filter_tool_result(
        "get_chat_info",
        {"ok": True, "id": BOTFATHER_ID, "title": "BotFather"},
    )
    assert denial["ok"] is False
    assert "blocked peer" in denial["error"]


def test_blocked_peers_get_chat_info_post_check_username_only(tmp_path):
    _write_acl_config(
        tmp_path,
        """
blocked_peers:
  - "@BotFather"
tokens: {}
""",
    )
    set_request_token("unlisted-token")
    denial = filter_tool_result(
        "get_chat_info",
        {"ok": True, "id": 999, "username": "BotFather"},
    )
    assert denial["ok"] is False
    assert "blocked peer" in denial["error"]


def test_blocked_peers_get_chat_info_post_check_id_and_username(
    blocked_peers_base_config,
):
    set_request_token("unlisted-token")
    denial = filter_tool_result(
        "get_chat_info",
        {"ok": True, "id": BOTFATHER_ID, "username": "BotFather"},
    )
    assert denial["ok"] is False


def test_blocked_peers_get_messages_post_check_message_chat_fallback(
    blocked_peers_base_config,
):
    set_request_token("unlisted-token")
    assert check_pre_tool_access("get_messages", {"chat_id": "@BotFather"}) is None
    denial = filter_tool_result(
        "get_messages",
        {
            "ok": True,
            "messages": [
                {"id": 1, "chat": {"id": BOTFATHER_ID, "username": "BotFather"}},
            ],
        },
    )
    assert denial["ok"] is False
    assert "blocked peer" in denial["error"]


def test_blocked_peers_invoke_mtproto_param_scan(blocked_peers_base_config):
    set_request_token("unlisted-token")
    denial = check_pre_tool_access(
        "invoke_mtproto",
        {"params_json": json.dumps({"peer": {"user_id": BOTFATHER_ID}})},
    )
    assert denial is not None
    assert "blocked peer" in denial["error"]


def test_blocked_peers_invoke_mtproto_invalid_json_fail_closed(
    blocked_peers_base_config,
):
    set_request_token("unlisted-token")
    denial = check_pre_tool_access(
        "invoke_mtproto",
        {"params_json": "{not-json"},
    )
    assert denial is not None
    assert "invalid params_json" in denial["error"].lower()


def test_blocked_peers_mtproto_http_merge_and_scan(blocked_peers_base_config):
    merged = merge_mtproto_request_params(
        {"peer": {"user_id": BOTFATHER_ID}},
        json.dumps({"limit": 10}),
    )
    denial = check_blocked_peer_mtproto_params(merged, operation="mtproto_api")
    assert denial is not None
    assert denial["operation"] == "mtproto_api"
    assert "blocked peer" in denial["error"]


def test_validate_acl_rejects_malformed_blocked_peers(tmp_path):
    _write_acl_config(
        tmp_path,
        """
blocked_peers:
  extend:
    - 123
tokens: {}
""",
    )
    with pytest.raises(AclConfigError, match="blocked_peers"):
        validate_acl_config()


def test_blocked_peers_skipped_when_acl_disabled(tmp_path):
    acl_file = tmp_path / "acl.yaml"
    acl_file.write_text(
        f"""
blocked_peers:
  - {BOTFATHER_ID}
tokens: {{}}
""",
        encoding="utf-8",
    )
    config = ServerConfig(_cli_parse_args=[])
    config.server_mode = ServerMode.HTTP_AUTH
    config.acl_enabled = False
    config.acl_config_path = str(acl_file)
    set_config(config)
    set_request_token("any")
    assert check_pre_tool_access("get_messages", {"chat_id": BOTFATHER_ID}) is None


def test_invoke_mtproto_allowed_when_flags_opt_in(tmp_path):
    _write_acl_config(
        tmp_path,
        """
tokens:
  power-user:
    chats:
      - me
    read_only: false
    allow_global_search: true
    allow_mtproto: true
""",
    )
    set_request_token("power-user")
    assert (
        check_pre_tool_access(
            "invoke_mtproto",
            {"method_full_name": "messages.GetHistory", "params_json": "{}"},
        )
        is None
    )
    assert check_mtproto_api_access("power-user", allow_dangerous=False) is None


def test_allow_mtproto_true_still_blocked_when_global_search_false(tmp_path):
    _write_acl_config(
        tmp_path,
        """
tokens:
  bot-like:
    chats:
      - -100999
    read_only: false
    allow_global_search: false
    allow_mtproto: true
""",
    )
    set_request_token("bot-like")
    denial = check_pre_tool_access("invoke_mtproto", {"params_json": "{}"})
    assert denial is not None
    assert "allow_global_search" in denial["error"].lower()


def test_deny_unlisted_tokens_blocks_unlisted_bearer(tmp_path):
    _write_acl_config(
        tmp_path,
        """
tokens:
  listed-only:
    chats:
      - me
    read_only: false
""",
    )
    config = get_config()
    config.acl_deny_unlisted_tokens = True
    set_config(config)
    set_request_token("not-in-acl-file")
    denial = check_pre_tool_access("find_chats", {"query": "x"})
    assert denial is not None
    assert "empty chat lane" in denial["error"].lower()


def test_deny_unlisted_tokens_false_preserves_full_access(tmp_path):
    _write_acl_config(
        tmp_path,
        """
tokens:
  listed-only:
    chats:
      - me
""",
    )
    config = get_config()
    config.acl_deny_unlisted_tokens = False
    set_config(config)
    set_request_token("not-in-acl-file")
    assert check_pre_tool_access("send_message", {"chat_id": -1}) is None


def test_allow_mtproto_does_not_bypass_blocked_peers(tmp_path):
    _write_acl_config(
        tmp_path,
        f"""
blocked_peers:
  - {BOTFATHER_ID}
tokens:
  power-user:
    chats:
      - me
    allow_mtproto: true
    allow_global_search: true
""",
    )
    set_request_token("power-user")
    denial = check_pre_tool_access(
        "invoke_mtproto",
        {"params_json": json.dumps({"user_id": BOTFATHER_ID})},
    )
    assert denial is not None
    assert "blocked peer" in denial["error"].lower()


def test_acl_load_warns_on_unknown_token_keys(tmp_path, caplog):
    import logging

    caplog.set_level(logging.WARNING)
    _write_acl_config(
        tmp_path,
        """
tokens:
  warn-token:
    chats:
      - me
    mystery_flag: true
""",
    )
    from src.server_components.session_acl import _load_acl_document

    _load_acl_document()
    assert any("unknown key" in rec.message.lower() for rec in caplog.records)
