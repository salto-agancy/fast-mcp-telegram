"""Tests for OIDC Elicitation State Machine."""

from datetime import UTC

import pytest

from src.auth import db
from src.auth.elicitation_state_machine import (
    TTL_SECONDS,
    ElicitState,
    record_retry,
    start_elicitation,
    submit_code,
    submit_password,
    submit_phone,
)
from src.auth.queries.setup_state import get_state_row, transition_state


@pytest.fixture
def oidc_key():
    return "test-oidc-key-123"


@pytest.fixture
def clean_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    db.run_migrations(db_path)
    return db_path


class TestStartElicitation:
    def test_creates_new_session(self, clean_db, oidc_key):
        result = start_elicitation(oidc_key, db_path=clean_db)
        assert result.success is True
        assert result.new_state == ElicitState.WAITING_PHONE
        assert "phone" in result.message.lower()

    def test_resumes_active_session(self, clean_db, oidc_key):
        start_elicitation(oidc_key, db_path=clean_db)
        result = start_elicitation(oidc_key, db_path=clean_db)
        assert result.success is True
        assert result.new_state == ElicitState.WAITING_PHONE

    def test_returns_completed(self, clean_db, oidc_key):
        start_elicitation(oidc_key, db_path=clean_db)
        transition_state(oidc_key, ElicitState.COMPLETED.value, db_path=clean_db)
        result = start_elicitation(oidc_key, db_path=clean_db)
        assert result.success is True
        assert result.new_state == ElicitState.COMPLETED

    def test_returns_failed(self, clean_db, oidc_key):
        start_elicitation(oidc_key, db_path=clean_db)
        transition_state(oidc_key, ElicitState.FAILED.value, db_path=clean_db)
        result = start_elicitation(oidc_key, db_path=clean_db)
        assert result.success is False
        assert result.new_state == ElicitState.FAILED

    def test_expires_old_session(self, clean_db, oidc_key):
        start_elicitation(oidc_key, db_path=clean_db)
        # Backdate updated_at using ISO format to simulate expiry
        from datetime import datetime, timedelta
        old_time = (datetime.now(UTC) - timedelta(seconds=TTL_SECONDS + 10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        import sqlite3
        conn = sqlite3.connect(clean_db)
        conn.execute("UPDATE setup_state SET updated_at = ? WHERE oidc_key = ?", (old_time, oidc_key))
        conn.commit()
        conn.close()

        result = start_elicitation(oidc_key, db_path=clean_db)
        assert result.success is False
        assert result.new_state == ElicitState.EXPIRED


class TestSubmitPhone:
    def test_transitions_to_waiting_code(self, clean_db, oidc_key):
        start_elicitation(oidc_key, db_path=clean_db)
        result = submit_phone(oidc_key, "+1234567890", db_path=clean_db)
        assert result.success is True
        assert result.new_state == ElicitState.WAITING_CODE

    def test_fails_without_session(self, clean_db, oidc_key):
        result = submit_phone(oidc_key, "+1234567890", db_path=clean_db)
        assert result.success is False
        assert result.new_state == ElicitState.FAILED

    def test_fails_wrong_state(self, clean_db, oidc_key):
        start_elicitation(oidc_key, db_path=clean_db)
        transition_state(oidc_key, ElicitState.WAITING_CODE.value, db_path=clean_db)
        result = submit_phone(oidc_key, "+1234567890", db_path=clean_db)
        assert result.success is False

    def test_stores_phone_number(self, clean_db, oidc_key):
        start_elicitation(oidc_key, db_path=clean_db)
        submit_phone(oidc_key, "+1234567890", db_path=clean_db)
        row = get_state_row(oidc_key, db_path=clean_db)
        assert row is not None
        assert row["phone_number"] == "+1234567890"


class TestSubmitCode:
    def test_completes_without_2fa(self, clean_db, oidc_key):
        start_elicitation(oidc_key, db_path=clean_db)
        submit_phone(oidc_key, "+1234567890", db_path=clean_db)
        result = submit_code(oidc_key, needs_2fa=False, db_path=clean_db)
        assert result.success is True
        assert result.new_state == ElicitState.COMPLETED

    def test_transitions_to_pass_with_2fa(self, clean_db, oidc_key):
        start_elicitation(oidc_key, db_path=clean_db)
        submit_phone(oidc_key, "+1234567890", db_path=clean_db)
        result = submit_code(oidc_key, needs_2fa=True, db_path=clean_db)
        assert result.success is True
        assert result.new_state == ElicitState.WAITING_PASS
        assert result.needs_2fa is True


class TestSubmitPassword:
    def test_completes_after_password(self, clean_db, oidc_key):
        start_elicitation(oidc_key, db_path=clean_db)
        submit_phone(oidc_key, "+1234567890", db_path=clean_db)
        transition_state(oidc_key, ElicitState.WAITING_PASS.value, db_path=clean_db)
        result = submit_password(oidc_key, db_path=clean_db)
        assert result.success is True
        assert result.new_state == ElicitState.COMPLETED


class TestRecordRetry:
    def test_allows_one_retry(self, clean_db, oidc_key):
        start_elicitation(oidc_key, db_path=clean_db)
        result = record_retry(oidc_key, db_path=clean_db)
        assert result.success is False
        assert result.new_state == ElicitState.WAITING_PHONE
        assert "0 attempt" in result.message

    def test_fails_after_max_retries(self, clean_db, oidc_key):
        start_elicitation(oidc_key, db_path=clean_db)
        record_retry(oidc_key, db_path=clean_db)
        result = record_retry(oidc_key, db_path=clean_db)
        assert result.success is False
        assert result.new_state == ElicitState.FAILED
