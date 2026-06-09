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

    def test_transition_state_preserves_existing_fields(self, db: str) -> None:
        """Transition does not overwrite unrelated columns when not provided."""
        create_state(
            oidc_key="k3",
            phone_number="+19998887777",
            db_path=db,
        )
        # Set initial metadata via direct SQL since create_state doesn't accept it
        from src.auth.db import get_connection
        with get_connection(db) as conn:
            conn.execute(
                "UPDATE setup_state SET metadata = ? WHERE oidc_key = ?",
                ('{"initial": true}', "k3"),
            )

        transition_state(
            oidc_key="k3",
            new_state="WAITING_CODE",
            tg_code_hash="preserve123",
            db_path=db,
        )

        states = get_active_states(older_than_seconds=0, db_path=db)
        row = next(s for s in states if s["oidc_key"] == "k3")
        assert row["state"] == "WAITING_CODE"
        assert row["tg_code_hash"] == "preserve123"
        assert row["phone_number"] == "+19998887777"
        assert row["metadata"] == '{"initial": true}'

    def test_transition_state_updates_metadata(self, db: str) -> None:
        """Transition stores metadata and preserves other fields."""
        create_state(
            oidc_key="k4",
            phone_number="+12223334444",
            db_path=db,
        )
        from src.auth.db import get_connection
        with get_connection(db) as conn:
            conn.execute(
                "UPDATE setup_state SET metadata = ? WHERE oidc_key = ?",
                ('{"old": "value"}', "k4"),
            )

        transition_state(
            oidc_key="k4",
            new_state="WAITING_CODE",
            tg_code_hash="meta123",
            metadata='{"new": "value", "flag": true}',
            db_path=db,
        )

        states = get_active_states(older_than_seconds=0, db_path=db)
        row = next(s for s in states if s["oidc_key"] == "k4")
        assert row["state"] == "WAITING_CODE"
        assert row["tg_code_hash"] == "meta123"
        assert row["phone_number"] == "+12223334444"
        assert row["metadata"] == '{"new": "value", "flag": true}'

    def test_get_active_states_excludes_non_waiting_without_ttl(self, db: str) -> None:
        """older_than_seconds=0 excludes COMPLETED/FAILED states."""
        create_state(oidc_key="waiting", db_path=db)
        transition_state(oidc_key="waiting", new_state="WAITING_CODE", db_path=db)

        # Create completed and failed states
        create_state(oidc_key="completed", db_path=db)
        transition_state(oidc_key="completed", new_state="COMPLETED", db_path=db)
        create_state(oidc_key="failed", db_path=db)
        transition_state(oidc_key="failed", new_state="FAILED", db_path=db)

        states = get_active_states(older_than_seconds=0, db_path=db)
        keys = {s["oidc_key"] for s in states}
        assert "waiting" in keys
        assert "completed" not in keys
        assert "failed" not in keys

    def test_ttl_query_includes_non_waiting_when_expired(self, db: str) -> None:
        """TTL path returns expired COMPLETED/FAILED rows."""
        create_state(oidc_key="old_completed", db_path=db)
        transition_state(oidc_key="old_completed", new_state="COMPLETED", db_path=db)
        create_state(oidc_key="old_failed", db_path=db)
        transition_state(oidc_key="old_failed", new_state="FAILED", db_path=db)
        create_state(oidc_key="fresh_completed", db_path=db)
        transition_state(oidc_key="fresh_completed", new_state="COMPLETED", db_path=db)

        from src.auth.db import get_connection
        with get_connection(db) as conn:
            old_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            conn.execute(
                "UPDATE setup_state SET updated_at = ? WHERE oidc_key IN (?, ?)",
                (old_time, "old_completed", "old_failed"),
            )

        expired = get_active_states(older_than_seconds=300, db_path=db)
        keys = {s["oidc_key"] for s in expired}
        assert "old_completed" in keys
        assert "old_failed" in keys
        assert "fresh_completed" not in keys

    def test_ttl_expiry_query(self, db: str) -> None:
        """get_active_states(older_than=5min) returns expired rows."""
        create_state(oidc_key="old", db_path=db)
        create_state(oidc_key="new", db_path=db)

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
