"""Tests for SessionACL principal_count() and read_only_count() methods (ADR 0005)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.client.connection import set_request_token
from src.config.server_config import ServerConfig, ServerMode, set_config
from src.server_components.session_acl import (
    clear_acl_cache,
    principal_count,
    read_only_count,
)


@pytest.fixture(autouse=True)
def _reset_acl():
    """Clear ACL cache and request token between tests."""
    clear_acl_cache()
    set_request_token(None)
    yield
    clear_acl_cache()
    set_request_token(None)


@pytest.fixture
def acl_config(tmp_path: Path):
    """Create a temporary ACL config file with 3 principals (1 read-only)."""
    acl_file = tmp_path / "acl.yaml"
    acl_file.write_text(
        """
principals:
  token-readonly:
    chats:
      - me
    read_only: true
  token-team:
    chats:
      - -1001234567890
      - "@workgroup"
    read_only: false
  token-auto:
    chats:
      - -100999
    read_only: false
""",
        encoding="utf-8",
    )
    config = ServerConfig(_cli_parse_args=[])
    config.server_mode = ServerMode.HTTP_AUTH
    config.acl_enabled = True
    config.acl_config_path = str(acl_file)
    set_config(config)
    return acl_file


def test_principal_count_returns_number_of_principals(acl_config):
    """principal_count() returns number of entries in ACL principals map."""
    assert principal_count() == 3


def test_principal_count_zero_when_acl_disabled():
    """principal_count() returns 0 when ACL is not enabled."""
    clear_acl_cache()
    config = ServerConfig(_cli_parse_args=[])
    config.server_mode = ServerMode.HTTP_AUTH
    config.acl_enabled = False
    set_config(config)

    assert principal_count() == 0


def test_principal_count_zero_when_no_config():
    """principal_count() returns 0 when no ACL config file exists."""
    clear_acl_cache()
    config = ServerConfig(_cli_parse_args=[])
    config.server_mode = ServerMode.HTTP_AUTH
    config.acl_enabled = True
    # Point to non-existent file
    config.acl_config_path = "/tmp/nonexistent-acl.yaml"
    set_config(config)

    # Should not raise — gracefully returns 0
    assert principal_count() == 0


def test_read_only_count_returns_read_only_principals(acl_config):
    """read_only_count() returns number of principals with read_only: true."""
    assert read_only_count() == 1  # only token-readonly has read_only: true


def test_read_only_count_zero_when_no_read_only(tmp_path: Path):
    """read_only_count() returns 0 when no principals have read_only."""
    clear_acl_cache()
    acl_file = tmp_path / "acl.yaml"
    acl_file.write_text(
        """
principals:
  token-alpha:
    chats: [me]
  token-beta:
    chats: [-100999]
""",
        encoding="utf-8",
    )
    config = ServerConfig(_cli_parse_args=[])
    config.server_mode = ServerMode.HTTP_AUTH
    config.acl_enabled = True
    config.acl_config_path = str(acl_file)
    set_config(config)
    clear_acl_cache()

    assert read_only_count() == 0


def test_read_only_count_zero_when_empty_principals(tmp_path: Path):
    """read_only_count() returns 0 when principals map is empty."""
    clear_acl_cache()
    acl_file = tmp_path / "acl.yaml"
    acl_file.write_text("principals: {}\n", encoding="utf-8")
    config = ServerConfig(_cli_parse_args=[])
    config.server_mode = ServerMode.HTTP_AUTH
    config.acl_enabled = True
    config.acl_config_path = str(acl_file)
    set_config(config)
    clear_acl_cache()

    assert principal_count() == 0
    assert read_only_count() == 0
