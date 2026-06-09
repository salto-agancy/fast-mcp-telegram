"""Tests for TelegramAuthService (Phase 3 — Telethon integration layer).

All Telethon calls are mocked; we test:
- send_code happy path + flood wait
- verify_code: success, invalid code, expired code, 2FA required
- verify_password: success, wrong password
- Lockfile concurrency guard
- Session file naming determinism
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.auth.telegram_auth_service import (
    SendCodeResult,
    SignInResult,
    TelegramAuthService,
)


@pytest.fixture
def svc(tmp_path):
    """Create a TelegramAuthService with temp session dir."""
    return TelegramAuthService(
        api_id=12345,
        api_hash="testhash",
        session_dir=str(tmp_path / "sessions"),
    )


# ---------------------------------------------------------------------------
# Session file naming
# ---------------------------------------------------------------------------

def test_session_file_deterministic(svc):
    """Same oidc_key always produces the same session path."""
    c1 = svc._client("key-abc")
    c2 = svc._client("key-abc")
    assert c1.session.filename == c2.session.filename


def test_session_file_different_keys(svc):
    """Different oidc_keys produce different session paths."""
    c1 = svc._client("key-abc")
    c2 = svc._client("key-xyz")
    assert c1.session.filename != c2.session.filename


# ---------------------------------------------------------------------------
# send_code
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_code_success(svc):
    mock_result = MagicMock(phone_code_hash="hash123")
    mock_client = AsyncMock()
    mock_client.send_code_request = AsyncMock(return_value=mock_result)
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()

    with patch.object(svc, "_client", return_value=mock_client):
        result = await svc.send_code("oidc-key-1", "+1234567890")

    assert isinstance(result, SendCodeResult)
    assert result.phone_code_hash == "hash123"
    assert result.next_state == "WAITING_CODE"
    mock_client.send_code_request.assert_called_once_with("+1234567890")


@pytest.mark.asyncio
async def test_send_code_flood_wait(svc):
    from telethon.errors import FloodWaitError
    from telethon.tl.types import CodeSettings
    from telethon.tl.functions.auth import SendCodeRequest

    mock_req = SendCodeRequest(
        phone_number="+1", api_id=1, api_hash="x",
        settings=CodeSettings(),
    )
    err = FloodWaitError(mock_req)
    err.seconds = 30

    mock_client = AsyncMock()
    mock_client.send_code_request = AsyncMock(side_effect=err)
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()

    with patch.object(svc, "_client", return_value=mock_client):
        with pytest.raises(RuntimeError, match="retry after 30s"):
            await svc.send_code("oidc-key-1", "+1234567890")


# ---------------------------------------------------------------------------
# verify_code
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verify_code_success(svc):
    mock_client = AsyncMock()
    mock_client.sign_in = AsyncMock()
    mock_client.export_session_string = AsyncMock(return_value="session-str")
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()

    with patch.object(svc, "_client", return_value=mock_client):
        result = await svc.verify_code("k1", "+123", "hash1", "12345")

    assert isinstance(result, SignInResult)
    assert result.success is True
    assert result.next_state == "COMPLETED"
    assert result.session_string == "session-str"


def _make_tl_error(error_cls):
    """Create a Telethon error with a dummy request (required positional arg)."""
    from telethon.tl.functions.auth import SignInRequest
    req = SignInRequest(phone_number="+1", phone_code_hash="h", phone_code="0")
    return error_cls(req)


@pytest.mark.asyncio
async def test_verify_code_2fa_required(svc):
    from telethon.errors import SessionPasswordNeededError

    mock_client = AsyncMock()
    mock_client.sign_in = AsyncMock(side_effect=_make_tl_error(SessionPasswordNeededError))
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()

    with patch.object(svc, "_client", return_value=mock_client):
        result = await svc.verify_code("k1", "+123", "hash1", "12345")

    assert result.success is True
    assert result.next_state == "WAITING_PASS"
    assert result.session_string is None


@pytest.mark.asyncio
async def test_verify_code_invalid(svc):
    from telethon.errors import PhoneCodeInvalidError

    mock_client = AsyncMock()
    mock_client.sign_in = AsyncMock(side_effect=_make_tl_error(PhoneCodeInvalidError))
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()

    with patch.object(svc, "_client", return_value=mock_client):
        result = await svc.verify_code("k1", "+123", "hash1", "00000")

    assert result.success is False
    assert result.next_state == "WAITING_CODE"
    assert "Invalid code" in result.error


@pytest.mark.asyncio
async def test_verify_code_expired(svc):
    from telethon.errors import PhoneCodeExpiredError

    mock_client = AsyncMock()
    mock_client.sign_in = AsyncMock(side_effect=_make_tl_error(PhoneCodeExpiredError))
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()

    with patch.object(svc, "_client", return_value=mock_client):
        result = await svc.verify_code("k1", "+123", "hash1", "12345")

    assert result.success is False
    assert result.next_state == "FAILED"
    assert "expired" in result.error.lower()


# ---------------------------------------------------------------------------
# verify_password
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verify_password_success(svc):
    mock_client = AsyncMock()
    mock_client.sign_in = AsyncMock()
    mock_client.export_session_string = AsyncMock(return_value="sess-pw")
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()

    with patch.object(svc, "_client", return_value=mock_client):
        result = await svc.verify_password("k1", "mypassword")

    assert result.success is True
    assert result.next_state == "COMPLETED"
    assert result.session_string == "sess-pw"


@pytest.mark.asyncio
async def test_verify_password_wrong(svc):
    from telethon.errors import PasswordHashInvalidError

    mock_client = AsyncMock()
    mock_client.sign_in = AsyncMock(side_effect=_make_tl_error(PasswordHashInvalidError))
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()

    with patch.object(svc, "_client", return_value=mock_client):
        result = await svc.verify_password("k1", "wrong")

    assert result.success is False
    assert result.next_state == "WAITING_PASS"
    assert "Invalid password" in result.error


# ---------------------------------------------------------------------------
# Concurrency: DB-based locking (state machine owns lock acquisition)
# ---------------------------------------------------------------------------
# Concurrency is enforced atomically in setup_state table (see
# elicitation_state_machine tests).  TelegramAuthService is stateless
# w.r.t. locking — caller must hold the DB lock before calling.
