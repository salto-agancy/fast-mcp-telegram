"""Tests for _validate_url_security SSRF prevention including DNS resolution."""

import socket
from unittest.mock import patch

import pytest

from src.config.server_config import ServerConfig, reset_cfg_for_tests, set_config
from src.tools.messages.security import _validate_url_security


@pytest.fixture(autouse=True)
def _reset_cfg():
    """Reset config to defaults before each test."""
    reset_cfg_for_tests()
    yield


def _enable_http() -> None:
    """Helper: set allow_http_urls=True in config."""
    config = ServerConfig()
    config.allow_http_urls = True
    set_config(config)


# ── Existing string-based checks (should work with or without DNS) ──


def test_empty_url() -> None:
    assert _validate_url_security("") == (False, "Empty URL not allowed")
    assert _validate_url_security("   ") == (False, "Empty URL not allowed")


def test_missing_hostname() -> None:
    assert _validate_url_security("https://") == (False, "Invalid URL: no hostname")


def test_https_allowed_by_default() -> None:
    is_safe, msg = _validate_url_security("https://example.com/file.png")
    assert is_safe, f"Expected safe, got: {msg}"


def test_http_blocked_by_default() -> None:
    is_safe, msg = _validate_url_security("http://example.com/file.png")
    assert not is_safe
    assert "HTTP URLs not allowed" in msg


def test_http_allowed_when_configured() -> None:
    _enable_http()
    is_safe, msg = _validate_url_security("http://example.com/file.png")
    assert is_safe, f"Expected safe, got: {msg}"


def test_localhost_string_checks() -> None:
    """Direct string name match (no DNS resolution needed)."""
    for host in ["localhost", "127.0.0.1", "0.0.0.0", "127.1", "127.0.1"]:
        is_safe, msg = _validate_url_security(f"https://{host}/file")
        assert not is_safe, f"Expected blocked for {host}, got safe"
        assert "Localhost access blocked" in msg, f"Unexpected msg for {host}: {msg}"


def test_ipv6_loopback_blocked() -> None:
    is_safe, msg = _validate_url_security("https://[::1]/file")
    assert not is_safe
    assert "Localhost access blocked" in msg


def test_suspicious_domains_blocked() -> None:
    for host in ["169.254.169.254", "metadata.google.internal"]:
        is_safe, msg = _validate_url_security(f"https://{host}/")
        assert not is_safe, f"Expected blocked for {host}"
        assert "Suspicious domain blocked" in msg


def test_block_private_ip_string_match() -> None:
    """Private IP as literal string — blocked by existing string check."""
    config = ServerConfig()
    config.block_private_ips = True
    set_config(config)
    is_safe, msg = _validate_url_security("https://10.0.0.1/file")
    assert not is_safe
    assert "Private IP access blocked" in msg


def test_block_link_local_ip_string_match() -> None:
    """Link-local IP as literal string."""
    config = ServerConfig()
    config.block_private_ips = True
    set_config(config)
    is_safe, _msg = _validate_url_security("https://169.254.1.1/file")
    assert not is_safe


# ── DNS resolution checks (the SSRF fix) ──


@patch("src.tools.messages.security.socket.getaddrinfo")
def test_dns_resolves_to_loopback_blocked(mock_getaddrinfo) -> None:
    """Domain that resolves to 127.0.0.1 should be blocked."""
    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))
    ]
    _enable_http()
    is_safe, msg = _validate_url_security("http://localtest.me/file")
    assert not is_safe
    assert "blocked" in msg
    assert "localtest.me" in msg
    assert "127.0.0.1" in msg


@patch("src.tools.messages.security.socket.getaddrinfo")
def test_dns_resolves_to_private_ip_blocked(mock_getaddrinfo) -> None:
    """Domain that resolves to 10.x.x.x should be blocked when block_private_ips=True."""
    config = ServerConfig()
    config.allow_http_urls = True
    config.block_private_ips = True
    set_config(config)

    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.5", 0))
    ]
    is_safe, msg = _validate_url_security("http://internal-service.local/file")
    assert not is_safe
    assert "blocked" in msg
    assert "internal-service.local" in msg
    assert "10.0.0.5" in msg


@patch("src.tools.messages.security.socket.getaddrinfo")
def test_dns_resolves_public_ip_allowed(mock_getaddrinfo) -> None:
    """Domain that resolves to a public IP should be allowed."""
    _enable_http()
    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))
    ]
    is_safe, msg = _validate_url_security("http://example.com/file")
    assert is_safe, f"Expected safe for public IP, got: {msg}"


@patch("src.tools.messages.security.socket.getaddrinfo")
def test_dns_resolution_failure_blocks(mock_getaddrinfo) -> None:
    """If DNS resolution fails, URL should be blocked."""
    mock_getaddrinfo.side_effect = socket.gaierror("Name or service not known")
    _enable_http()
    is_safe, msg = _validate_url_security(
        "http://nonexistent-domain-xyzzy-12345.com/file"
    )
    assert not is_safe
    assert "DNS resolution failed" in msg


@patch("src.tools.messages.security.socket.getaddrinfo")
def test_dns_resolves_to_link_local_blocked(mock_getaddrinfo) -> None:
    """Domain that resolves to 169.254.x.x should be blocked."""
    config = ServerConfig()
    config.allow_http_urls = True
    config.block_private_ips = True
    set_config(config)

    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.1.1", 0))
    ]
    is_safe, msg = _validate_url_security("http://link-local-spoof.example/file")
    assert not is_safe
    assert "blocked" in msg


@patch("src.tools.messages.security.socket.getaddrinfo")
def test_dns_resolves_to_ipv6_loopback_blocked(mock_getaddrinfo) -> None:
    """Domain that resolves to IPv6 loopback (::1) should be blocked."""
    mock_getaddrinfo.return_value = [
        (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::1", 0, 0, 0))
    ]
    _enable_http()
    is_safe, msg = _validate_url_security("http://ipv6-localtest.example/file")
    assert not is_safe
    assert "blocked" in msg


@patch("src.tools.messages.security.socket.getaddrinfo")
def test_dns_multiple_ips_some_bad_all_blocked(mock_getaddrinfo) -> None:
    """If any resolved IP is loopback, the URL is blocked (even with public IPs)."""
    _enable_http()
    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0)),
    ]
    is_safe, msg = _validate_url_security("http://multi-ip.example/file")
    assert not is_safe
    assert "blocked" in msg
    assert "127.0.0.1" in msg
