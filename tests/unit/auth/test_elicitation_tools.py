"""Tests for OIDC elicitation FastMCP tools.

Tests the async tool functions that wire the state machine to Telethon.
Uses mocked TelegramAuthService to avoid real Telegram API calls.
"""

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.auth import db
from src.auth.elicitation_state_machine import ElicitState
from src.auth.elicitation_tools import (
    oidc_setup_code,
    oidc_setup_password,
    oidc_setup_phone,
    oidc_setup_start,
)
from src.auth.queries import oidc_identity as id_queries
from src.auth.queries.oidc_identity import make_oidc_key
from src.auth.queries.setup_state import get_state_row
from src.auth.telegram_auth_service import SendCodeResult, SignInResult

ISSUER = "https://auth.example.com/"


@pytest.fixture
def test_db(tmp_path):
    """Create a fresh test database."""
    db_path = str(tmp_path / "test.db")
    db.run_migrations(db_path)
    return db_path


@pytest.fixture
def mock_auth_service():
    """Mock TelegramAuthService to avoid real Telegram calls."""
    with patch("src.auth.elicitation_tools._get_auth_service") as mock_get:
        service = AsyncMock()
        mock_get.return_value = service
        yield service


def _session_path(oidc_key: str) -> Path:
    """Compute the on-disk session file path for an oidc_key."""
    safe_name = hashlib.sha256(oidc_key.encode()).hexdigest()[:16]
    session_dir = os.environ.get("TG_SESSION_DIR", ".sessions")
    return Path(session_dir) / f"oidc_{safe_name}.session"


class TestOidcSetupStart:
    @pytest.mark.asyncio
    async def test_start_new_session(self, test_db):
        result = await oidc_setup_start("test-sub", ISSUER, db_path=test_db)
        assert result["success"] is True
        assert result["state"] == ElicitState.WAITING_PHONE.value
        assert "phone" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_start_already_mapped(self, test_db):
        oidc_key = make_oidc_key("mapped-sub", ISSUER)
        id_queries.insert_identity(
            oidc_key=oidc_key,
            oidc_sub="mapped-sub",
            oidc_issuer=ISSUER,
            telegram_user_id=123,
            telegram_username="testuser",
            db_path=test_db,
        )
        result = await oidc_setup_start("mapped-sub", ISSUER, db_path=test_db)
        assert result["success"] is True
        assert result["state"] == ElicitState.COMPLETED.value
        assert "already linked" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_resume_existing_session(self, test_db):
        await oidc_setup_start("resume-sub", ISSUER, db_path=test_db)
        result = await oidc_setup_start("resume-sub", ISSUER, db_path=test_db)
        assert result["success"] is True
        assert result["state"] == ElicitState.WAITING_PHONE.value


class TestOidcSetupPhone:
    @pytest.mark.asyncio
    async def test_submit_phone_success(self, test_db, mock_auth_service):
        oidc_key = make_oidc_key("phone-sub", ISSUER)
        await oidc_setup_start("phone-sub", ISSUER, db_path=test_db)

        mock_auth_service.send_code.return_value = SendCodeResult(
            phone_code_hash="hash123",
            next_state="WAITING_CODE",
        )

        result = await oidc_setup_phone(
            "phone-sub", ISSUER, "+1234567890", db_path=test_db
        )
        assert result["success"] is True
        assert result["state"] == ElicitState.WAITING_CODE.value

        state = get_state_row(oidc_key, db_path=test_db)
        meta = json.loads(state["metadata"])
        assert meta["phone_code_hash"] == "hash123"
        assert meta["phone_number"] == "+1234567890"

    @pytest.mark.asyncio
    async def test_submit_phone_no_session(self, test_db, mock_auth_service):
        result = await oidc_setup_phone(
            "no-session-sub", ISSUER, "+1234567890", db_path=test_db
        )
        assert result["success"] is False
        assert result["state"] == ElicitState.FAILED.value

    @pytest.mark.asyncio
    async def test_submit_phone_send_code_fails(self, test_db, mock_auth_service):
        await oidc_setup_start("fail-phone-sub", ISSUER, db_path=test_db)
        mock_auth_service.send_code.side_effect = RuntimeError("Flood wait")

        result = await oidc_setup_phone(
            "fail-phone-sub", ISSUER, "+1234567890", db_path=test_db
        )
        assert result["success"] is False


class TestOidcSetupCode:
    @pytest.mark.asyncio
    async def test_verify_code_success_no_2fa(self, test_db, mock_auth_service):
        oidc_key = make_oidc_key("code-sub", ISSUER)
        await oidc_setup_start("code-sub", ISSUER, db_path=test_db)
        mock_auth_service.send_code.return_value = SendCodeResult(
            phone_code_hash="hash456", next_state="WAITING_CODE"
        )
        await oidc_setup_phone("code-sub", ISSUER, "+1234567890", db_path=test_db)

        mock_auth_service.verify_code.return_value = SignInResult(
            success=True,
            next_state="COMPLETED",
            session_string="session_str_abc",
            user_id=42,
            username="codetest",
        )

        result = await oidc_setup_code("code-sub", ISSUER, "12345", db_path=test_db)
        assert result["success"] is True
        assert result["state"] == ElicitState.COMPLETED.value

        identity = id_queries.get_identity(oidc_key, db_path=test_db)
        assert identity is not None
        assert identity["telegram_user_id"] == 42
        assert identity["telegram_username"] == "codetest"

        session_file = _session_path(oidc_key)
        assert session_file.exists()
        assert session_file.read_text() == "session_str_abc"

    @pytest.mark.asyncio
    async def test_verify_code_needs_2fa(self, test_db, mock_auth_service):
        await oidc_setup_start("2fa-sub", ISSUER, db_path=test_db)
        mock_auth_service.send_code.return_value = SendCodeResult(
            phone_code_hash="hash789", next_state="WAITING_CODE"
        )
        await oidc_setup_phone("2fa-sub", ISSUER, "+1234567890", db_path=test_db)

        mock_auth_service.verify_code.return_value = SignInResult(
            success=True,
            next_state="WAITING_PASS",
        )

        result = await oidc_setup_code("2fa-sub", ISSUER, "12345", db_path=test_db)
        assert result["success"] is True
        assert result["state"] == ElicitState.WAITING_PASS.value
        assert result["needs_2fa"] is True

    @pytest.mark.asyncio
    async def test_verify_code_invalid(self, test_db, mock_auth_service):
        await oidc_setup_start("bad-code-sub", ISSUER, db_path=test_db)
        mock_auth_service.send_code.return_value = SendCodeResult(
            phone_code_hash="hashX", next_state="WAITING_CODE"
        )
        await oidc_setup_phone("bad-code-sub", ISSUER, "+1234567890", db_path=test_db)

        mock_auth_service.verify_code.return_value = SignInResult(
            success=False,
            next_state="WAITING_CODE",
            error="Invalid code",
        )

        result = await oidc_setup_code("bad-code-sub", ISSUER, "00000", db_path=test_db)
        assert result["success"] is False


class TestOidcSetupPassword:
    @pytest.mark.asyncio
    async def test_verify_password_success(self, test_db, mock_auth_service):
        oidc_key = make_oidc_key("pass-sub", ISSUER)
        await oidc_setup_start("pass-sub", ISSUER, db_path=test_db)
        mock_auth_service.send_code.return_value = SendCodeResult(
            phone_code_hash="hashP", next_state="WAITING_CODE"
        )
        await oidc_setup_phone("pass-sub", ISSUER, "+1234567890", db_path=test_db)
        mock_auth_service.verify_code.return_value = SignInResult(
            success=True, next_state="WAITING_PASS"
        )
        await oidc_setup_code("pass-sub", ISSUER, "12345", db_path=test_db)

        mock_auth_service.verify_password.return_value = SignInResult(
            success=True,
            next_state="COMPLETED",
            session_string="session_2fa_done",
            user_id=99,
            username="twofauser",
        )

        result = await oidc_setup_password(
            "pass-sub", ISSUER, "my_password", db_path=test_db
        )
        assert result["success"] is True
        assert result["state"] == ElicitState.COMPLETED.value

        identity = id_queries.get_identity(oidc_key, db_path=test_db)
        assert identity is not None
        assert identity["telegram_user_id"] == 99

        session_file = _session_path(oidc_key)
        assert session_file.exists()
        assert session_file.read_text() == "session_2fa_done"

    @pytest.mark.asyncio
    async def test_verify_password_invalid(self, test_db, mock_auth_service):
        await oidc_setup_start("bad-pass-sub", ISSUER, db_path=test_db)
        mock_auth_service.send_code.return_value = SendCodeResult(
            phone_code_hash="hashBP", next_state="WAITING_CODE"
        )
        await oidc_setup_phone("bad-pass-sub", ISSUER, "+1234567890", db_path=test_db)
        mock_auth_service.verify_code.return_value = SignInResult(
            success=True, next_state="WAITING_PASS"
        )
        await oidc_setup_code("bad-pass-sub", ISSUER, "12345", db_path=test_db)

        mock_auth_service.verify_password.return_value = SignInResult(
            success=False,
            next_state="WAITING_PASS",
            error="Invalid password",
        )

        result = await oidc_setup_password(
            "bad-pass-sub", ISSUER, "wrong_pass", db_path=test_db
        )
        assert result["success"] is False
