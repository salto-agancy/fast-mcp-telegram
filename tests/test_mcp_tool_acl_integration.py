"""End-to-end tests for mcp_tool_with_restrictions ACL enforcement."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.config.server_config import ServerConfig, ServerMode, set_config
from src.server_components.session_acl import clear_acl_cache
from src.server_components.tools_register import mcp_tool_with_restrictions
from tests.conftest import make_access_token


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


@pytest.mark.asyncio
async def test_read_only_token_blocks_send_message_via_decorator_chain(acl_config):
    """Read-only ACL must block send_message before the tool body runs."""
    tool_called = AsyncMock(return_value={"ok": True, "message_id": 1})

    @mcp_tool_with_restrictions("send_message")
    async def send_message(chat_id, message):
        return await tool_called(chat_id=chat_id, message=message)

    with patch(
        "fastmcp.server.dependencies.get_access_token",
        return_value=make_access_token("token-readonly"),
    ):
        result = await send_message(chat_id="me", message="hello")

    assert result["ok"] is False
    assert "read-only" in result["error"].lower()
    tool_called.assert_not_called()


@pytest.mark.asyncio
async def test_chat_whitelist_blocks_forbidden_chat_via_decorator_chain(acl_config):
    """Chat whitelist pre-check must block get_messages for non-listed chats."""
    tool_called = AsyncMock(return_value={"ok": True, "messages": []})

    @mcp_tool_with_restrictions("get_messages")
    async def get_messages(chat_id, limit=50):
        return await tool_called(chat_id=chat_id, limit=limit)

    with patch(
        "fastmcp.server.dependencies.get_access_token",
        return_value=make_access_token("token-team"),
    ):
        result = await get_messages(chat_id=-1000000)

    assert result["ok"] is False
    assert "not in the allowed list" in result["error"]
    tool_called.assert_not_called()


@pytest.mark.asyncio
async def test_chat_whitelist_allows_listed_chat_via_decorator_chain(acl_config):
    """Listed chats pass ACL pre-check and reach the tool body."""
    tool_called = AsyncMock(return_value={"ok": True, "messages": []})

    @mcp_tool_with_restrictions("get_messages")
    async def get_messages(chat_id, limit=50):
        return await tool_called(chat_id=chat_id, limit=limit)

    with patch(
        "fastmcp.server.dependencies.get_access_token",
        return_value=make_access_token("token-team"),
    ):
        result = await get_messages(chat_id=-1001234567890)

    assert result == {"ok": True, "messages": []}
    tool_called.assert_awaited_once_with(chat_id=-1001234567890, limit=50)


@pytest.fixture
def empty_lane_acl_config(tmp_path: Path):
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


@pytest.mark.asyncio
async def test_empty_lane_blocks_find_chats_via_decorator_chain(empty_lane_acl_config):
    """Empty chat lane must hard-deny find_chats before the tool body runs."""
    tool_called = AsyncMock(return_value={"ok": True, "chats": [{"id": -100123, "title": "Leaked"}]})

    @mcp_tool_with_restrictions("find_chats")
    async def find_chats(query, limit=50):
        return await tool_called(query=query, limit=limit)

    with patch(
        "fastmcp.server.dependencies.get_access_token",
        return_value=make_access_token("empty-lane"),
    ):
        result = await find_chats(query="work")

    assert result["ok"] is False
    assert "empty chat lane" in result["error"].lower()
    tool_called.assert_not_called()


@pytest.mark.asyncio
async def test_chat_whitelist_blocks_invoke_mtproto_via_decorator_chain(acl_config):
    """Chat whitelist must block invoke_mtproto before the tool body runs."""
    tool_called = AsyncMock(return_value={"ok": True, "result": {}})

    @mcp_tool_with_restrictions("invoke_mtproto", allow_bot_sessions=True)
    async def invoke_mtproto(method_full_name, params_json, allow_dangerous=False):
        return await tool_called(
            method_full_name=method_full_name,
            params_json=params_json,
            allow_dangerous=allow_dangerous,
        )

    with patch(
        "fastmcp.server.dependencies.get_access_token",
        return_value=make_access_token("token-team"),
    ):
        result = await invoke_mtproto(
            method_full_name="messages.GetHistory",
            params_json="{}",
        )

    assert result["ok"] is False
    assert "listed in the acl config" in result["error"].lower()
    tool_called.assert_not_called()
