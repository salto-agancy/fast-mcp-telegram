"""Tests for elicitation setup state persistence with TTL."""

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.auth.db import run_migrations
from src.auth.queries.setup_state import (
    create_state,
    delete_expired,
    get_active_states,
    increment_retry_count,
    transition_state,
)


@pytest.fixture
def db(tmp_path: Path) -> str:
    """Create and migrate a temporary database."""
    path = str(tmp_path / "test_state.db")
    run_migrations(path)
    return path


class TestSetupStatePersistence:
    """Setup state machine table operations."""

    def test_create_setup_state(self, db: str) -> None:
        """Initial state is WAITING_PHONE."""
        create_state(oidc_key="k1", phone_number="+1234567890", db_path=db)

        states = get_active_states(older_than_seconds=0, db_path=db)
        assert len(states) == 1
        assert states[0]["state"] == "WAITING_PHONE"
        assert states[0]["phone_number"] == "+1234567890"
        assert states[0]["retry_count"] == 0

    def test_transition_state(self, db: str) -> None:
        """WAITING_PHONE → WAITING_CODE updates row."""
        create_state(oidc_key="k2", db_path=db)
        transition_state(
            oidc_key="k2",
            new_state="WAITING_CODE",
            tg_code_hash="abc123",
            db_path=db,
        )

        states = get_active_states(older_than_seconds=0, db_path=db)
        row = next(s for s in states if s["oidc_key"] == "k2")
        assert row["state"] == "WAITING_CODE"
        assert row["tg_code_hash"] == "abc123"

    def test_ttl_expiry_query(self, db: str) -> None:
        """get_active_states(older_than=5min) returns expired rows."""
        create_state(oidc_key="old", db_path=db)
        create_state(oidc_key="new", db_path=db)

        # Manually backdate 'old' row
        from src.auth.db import get_connection
        with get_connection(db) as conn:
            old_time = (datetime.now(timezone.utc) - timedelta(minutes=6)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            conn.execute(
                "UPDATE setup_state SET updated_at = ? WHERE oidc_key = ?",
                (old_time, "old"),
            )

        expired = get_active_states(older_than_seconds=300, db_path=db)
        keys = {s["oidc_key"] for s in expired}
        assert "old" in keys
        assert "new" not in keys

    def test_delete_expired_states(self, db: str) -> None:
        """Removes expired rows and returns count."""
        create_state(oidc_key="exp1", db_path=db)
        create_state(oidc_key="exp2", db_path=db)
        create_state(oidc_key="fresh", db_path=db)

        from src.auth.db import get_connection
        with get_connection(db) as conn:
            old_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            conn.execute(
                "UPDATE setup_state SET updated_at = ? WHERE oidc_key IN ('exp1','exp2')",
                (old_time,),
            )

        count = delete_expired(older_than_seconds=300, db_path=db)
        assert count == 2

        remaining = get_active_states(older_than_seconds=0, db_path=db)
        assert len(remaining) == 1
        assert remaining[0]["oidc_key"] == "fresh"

    def test_retry_count_increment(self, db: str) -> None:
        """Increments retry_count on failed attempt."""
        create_state(oidc_key="retry", db_path=db)

        increment_retry_count("retry", db_path=db)
        increment_retry_count("retry", db_path=db)

        states = get_active_states(older_than_seconds=0, db_path=db)
        row = next(s for s in states if s["oidc_key"] == "retry")
        assert row["retry_count"] == 2
