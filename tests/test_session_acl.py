"""Tests for opt-in session ACL enforcement."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.client.connection import set_request_token
from src.config.server_config import ServerConfig, ServerMode, set_config
from src.server_components.session_acl import (
    TokenAclRule,
    check_pre_tool_access,
    clear_acl_cache,
    filter_tool_result,
)


@pytest.fixture(autouse=True)
def _reset_acl():
    clear_acl_cache()
    yield
    clear_acl_cache()


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
