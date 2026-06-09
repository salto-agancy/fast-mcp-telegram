"""Tests for OIDC elicitation FastMCP tools.

Tests the async tool functions that wire the state machine to Telethon.
Uses mocked TelegramAuthService to avoid real Telegram API calls.
"""

import json
import os
import pytest
from unittest.mock import AsyncMock, patch

from src.auth.elicitation_tools import (
    oidc_setup_start,
    oidc_setup_phone,
    oidc_setup_code,
    oidc_setup_password,
)
from src.auth.elicitation_state_machine import ElicitState
from src.auth.telegram_auth_service import SendCodeResult, SignInResult
from src.auth.queries import oidc_identity as id_queries
from src.auth import db


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


class TestOidcSetupStart:
    @pytest.mark.asyncio
    async def test_start_new_session(self, test_db):
        result = await oidc_setup_start("test-key:issuer", db_path=test_db)
        assert result["success"] is True
        assert result["state"] == ElicitState.WAITING_PHONE.value
        assert "phone" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_start_already_mapped(self, test_db):
        # Create identity mapping first
        id_queries.insert_identity(
            oidc_key="mapped-key:issuer",
            oidc_sub="mapped-key",
            oidc_issuer="issuer",
            telegram_user_id=123,
            telegram_username="testuser",
            db_path=test_db,
        )
        result = await oidc_setup_start("mapped-key:issuer", db_path=test_db)
        assert result["success"] is True
        assert result["state"] == ElicitState.COMPLETED.value
        assert "already linked" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_resume_existing_session(self, test_db):
        # Start once
        await oidc_setup_start("resume-key:issuer", db_path=test_db)
        # Resume
        result = await oidc_setup_start("resume-key:issuer", db_path=test_db)
        assert result["success"] is True
        assert result["state"] == ElicitState.WAITING_PHONE.value


class TestOidcSetupPhone:
    @pytest.mark.asyncio
    async def test_submit_phone_success(self, test_db, mock_auth_service):
        # Start session first
        await oidc_setup_start("phone-key:issuer", db_path=test_db)

        # Mock send_code
        mock_auth_service.send_code.return_value = SendCodeResult(
            phone_code_hash="hash123",
            next_state="WAITING_CODE",
        )

        result = await oidc_setup_phone(
            "phone-key:issuer", "+1234567890", db_path=test_db
        )
        assert result["success"] is True
        assert result["state"] == ElicitState.WAITING_CODE.value

        # Verify metadata stored
        state = db.get_setup_state("phone-key:issuer", db_path=test_db)
        meta = json.loads(state["metadata"])
        assert meta["phone_code_hash"] == "hash123"
        assert meta["phone_number"] == "+1234567890"

    @pytest.mark.asyncio
    async def test_submit_phone_no_session(self, test_db, mock_auth_service):
        result = await oidc_setup_phone(
            "no-session:issuer", "+1234567890", db_path=test_db
        )
        assert result["success"] is False
        assert result["state"] == ElicitState.FAILED.value

    @pytest.mark.asyncio
    async def test_submit_phone_send_code_fails(self, test_db, mock_auth_service):
        await oidc_setup_start("fail-phone:issuer", db_path=test_db)
        mock_auth_service.send_code.side_effect = RuntimeError("Flood wait")

        result = await oidc_setup_phone(
            "fail-phone:issuer", "+1234567890", db_path=test_db
        )
        assert result["success"] is False


class TestOidcSetupCode:
    @pytest.mark.asyncio
    async def test_verify_code_success_no_2fa(self, test_db, mock_auth_service):
        # Setup: start + submit phone
        await oidc_setup_start("code-key:issuer", db_path=test_db)
        mock_auth_service.send_code.return_value = SendCodeResult(
            phone_code_hash="hash456", next_state="WAITING_CODE"
        )
        await oidc_setup_phone("code-key:issuer", "+1234567890", db_path=test_db)

        # Mock verify_code — no 2FA
        mock_auth_service.verify_code.return_value = SignInResult(
            success=True,
            next_state="COMPLETED",
            session_string="session_str_abc",
            user_id=42,
            username="codetest",
        )

        result = await oidc_setup_code("code-key:issuer", "12345", db_path=test_db)
        assert result["success"] is True
        assert result["state"] == ElicitState.COMPLETED.value

        # Verify identity created
        identity = id_queries.get_identity("code-key:issuer", db_path=test_db)
        assert identity is not None
        assert identity["telegram_user_id"] == 42
        assert identity["telegram_username"] == "codetest"

        # Verify session file written to disk
        import hashlib
        safe_name = hashlib.sha256("code-key:issuer".encode()).hexdigest()[:16]
        session_dir = os.environ.get("TG_SESSION_DIR", ".sessions")
        from pathlib import Path
        session_file = Path(session_dir) / f"oidc_{safe_name}.session"
        assert session_file.exists()
        assert session_file.read_text() == "session_str_abc"

    @pytest.mark.asyncio
    async def test_verify_code_needs_2fa(self, test_db, mock_auth_service):
        await oidc_setup_start("2fa-key:issuer", db_path=test_db)
        mock_auth_service.send_code.return_value = SendCodeResult(
            phone_code_hash="hash789", next_state="WAITING_CODE"
        )
        await oidc_setup_phone("2fa-key:issuer", "+1234567890", db_path=test_db)

        # Mock verify_code — 2FA needed
        mock_auth_service.verify_code.return_value = SignInResult(
            success=True,
            next_state="WAITING_PASS",
        )

        result = await oidc_setup_code("2fa-key:issuer", "12345", db_path=test_db)
        assert result["success"] is True
        assert result["state"] == ElicitState.WAITING_PASS.value
        assert result["needs_2fa"] is True

    @pytest.mark.asyncio
    async def test_verify_code_invalid(self, test_db, mock_auth_service):
        await oidc_setup_start("bad-code:issuer", db_path=test_db)
        mock_auth_service.send_code.return_value = SendCodeResult(
            phone_code_hash="hashX", next_state="WAITING_CODE"
        )
        await oidc_setup_phone("bad-code:issuer", "+1234567890", db_path=test_db)

        mock_auth_service.verify_code.return_value = SignInResult(
            success=False,
            next_state="WAITING_CODE",
            error="Invalid code",
        )

        result = await oidc_setup_code("bad-code:issuer", "00000", db_path=test_db)
        assert result["success"] is False


class TestOidcSetupPassword:
    @pytest.mark.asyncio
    async def test_verify_password_success(self, test_db, mock_auth_service):
        # Setup full flow to WAITING_PASS
        await oidc_setup_start("pass-key:issuer", db_path=test_db)
        mock_auth_service.send_code.return_value = SendCodeResult(
            phone_code_hash="hashP", next_state="WAITING_CODE"
        )
        await oidc_setup_phone("pass-key:issuer", "+1234567890", db_path=test_db)
        mock_auth_service.verify_code.return_value = SignInResult(
            success=True, next_state="WAITING_PASS"
        )
        await oidc_setup_code("pass-key:issuer", "12345", db_path=test_db)

        # Mock password verification
        mock_auth_service.verify_password.return_value = SignInResult(
            success=True,
            next_state="COMPLETED",
            session_string="session_2fa_done",
            user_id=99,
            username="twofauser",
        )

        result = await oidc_setup_password(
            "pass-key:issuer", "my_password", db_path=test_db
        )
        assert result["success"] is True
        assert result["state"] == ElicitState.COMPLETED.value

        identity = id_queries.get_identity("pass-key:issuer", db_path=test_db)
        assert identity is not None
        assert identity["telegram_user_id"] == 99

        # Verify session file written to disk
        import hashlib
        safe_name = hashlib.sha256("pass-key:issuer".encode()).hexdigest()[:16]
        session_dir = os.environ.get("TG_SESSION_DIR", ".sessions")
        from pathlib import Path
        session_file = Path(session_dir) / f"oidc_{safe_name}.session"
        assert session_file.exists()
        assert session_file.read_text() == "session_2fa_done"

    @pytest.mark.asyncio
    async def test_verify_password_invalid(self, test_db, mock_auth_service):
        # Setup to WAITING_PASS
        await oidc_setup_start("bad-pass:issuer", db_path=test_db)
        mock_auth_service.send_code.return_value = SendCodeResult(
            phone_code_hash="hashBP", next_state="WAITING_CODE"
        )
        await oidc_setup_phone("bad-pass:issuer", "+1234567890", db_path=test_db)
        mock_auth_service.verify_code.return_value = SignInResult(
            success=True, next_state="WAITING_PASS"
        )
        await oidc_setup_code("bad-pass:issuer", "12345", db_path=test_db)

        mock_auth_service.verify_password.return_value = SignInResult(
            success=False,
            next_state="WAITING_PASS",
            error="Invalid password",
        )

        result = await oidc_setup_password(
            "bad-pass:issuer", "wrong_pass", db_path=test_db
        )
        assert result["success"] is False
