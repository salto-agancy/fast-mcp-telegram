"""Tests for inactivity-based session cleanup."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.client.connection import (
    _INACTIVE_SESSION_DAYS,
    _SESSION_TRACKING_FILE,
    _cleanup_inactive_sessions,
    _load_session_tracking,
    _record_connection_failure,
    _save_session_tracking,
    _tracking_file_path,
    _update_last_active,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tracking_path(tmp_path):
    """Return the expected tracking file path inside tmp_path."""
    return tmp_path / _SESSION_TRACKING_FILE


_TOKEN = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFg"  # 43-char valid token
_TOKEN_B = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFh"  # 43-char variant


def make_session_file(session_dir: Path, token: str):
    """Create a fake Telethon .session file for *token*."""
    path = session_dir / f"{token}.session"
    path.write_text("fake session data")
    return path


# ---------------------------------------------------------------------------
# _tracking_file_path
# ---------------------------------------------------------------------------


class TestTrackingFilePath:
    """Path construction for the tracking file."""

    def test_returns_session_dir_plus_filename(self, tmp_path):
        with patch("src.client.connection.SESSION_DIR", tmp_path):
            result = _tracking_file_path()
        assert result == tmp_path / "session_tracking.json"


# ---------------------------------------------------------------------------
# _load_session_tracking
# ---------------------------------------------------------------------------


class TestLoadSessionTracking:
    """Loading tracking data from disk."""

    def test_returns_empty_dict_when_file_missing(self, tmp_path):
        with patch("src.client.connection.SESSION_DIR", tmp_path):
            data = _load_session_tracking()
        assert data == {}

    def test_returns_empty_dict_when_file_corrupt(self, tmp_path, tracking_path):
        tracking_path.write_text("not json", encoding="utf-8")
        with patch("src.client.connection.SESSION_DIR", tmp_path):
            data = _load_session_tracking()
        assert data == {}

    def test_loads_valid_json(self, tmp_path, tracking_path):
        expected = {"tok_a": {"last_active": 100.0}}
        tracking_path.write_text(json.dumps(expected), encoding="utf-8")
        with patch("src.client.connection.SESSION_DIR", tmp_path):
            data = _load_session_tracking()
        assert data == expected


# ---------------------------------------------------------------------------
# _save_session_tracking
# ---------------------------------------------------------------------------


class TestSaveSessionTracking:
    """Persisting tracking data to disk."""

    def test_writes_json_file(self, tmp_path, tracking_path):
        data = {"tok_a": {"last_active": 100.0, "failure_count": 0}}
        with patch("src.client.connection.SESSION_DIR", tmp_path):
            _save_session_tracking(data)
        assert tracking_path.is_file()
        loaded = json.loads(tracking_path.read_text(encoding="utf-8"))
        assert loaded == data


# ---------------------------------------------------------------------------
# _update_last_active
# ---------------------------------------------------------------------------


class TestUpdateLastActive:
    """Updating the last_active timestamp for a token."""

    @pytest.mark.asyncio
    async def test_sets_timestamp_on_existing_entry(self, tmp_path, tracking_path):
        token = "tok_a"
        initial = {
            token: {"last_active": 42.0, "failure_count": 0, "last_failure": None}
        }
        tracking_path.write_text(json.dumps(initial), encoding="utf-8")
        with patch("src.client.connection.SESSION_DIR", tmp_path):
            await _update_last_active(token)
        loaded = json.loads(tracking_path.read_text(encoding="utf-8"))
        assert loaded[token]["last_active"] > 42.0  # advanced
        assert loaded[token]["failure_count"] == 0

    @pytest.mark.asyncio
    async def test_creates_new_entry_when_missing(self, tmp_path, tracking_path):
        token = "tok_new"
        with patch("src.client.connection.SESSION_DIR", tmp_path):
            await _update_last_active(token)
        loaded = json.loads(tracking_path.read_text(encoding="utf-8"))
        assert loaded[token]["last_active"] is not None
        assert loaded[token]["failure_count"] == 0
        assert loaded[token]["last_failure"] is None


# ---------------------------------------------------------------------------
# _cleanup_inactive_sessions
# ---------------------------------------------------------------------------


class TestCleanupInactiveSessions:
    """Main cleanup function — deletes session files older than 30 days."""

    @pytest.mark.asyncio
    async def test_deletes_session_when_last_active_older_than_cutoff(self, tmp_path):
        """Session with last_active >30 days ago is deleted."""
        now = 1_000_000_000.0
        cutoff_secs = _INACTIVE_SESSION_DAYS * 86400
        old_time = now - cutoff_secs - 100  # well before cutoff

        token = _TOKEN
        tracking = {
            token: {"last_active": old_time, "failure_count": 0, "last_failure": None}
        }
        tracking_path = tmp_path / "session_tracking.json"
        tracking_path.write_text(json.dumps(tracking), encoding="utf-8")
        session_file = make_session_file(tmp_path, token)

        assert session_file.is_file()  # sanity

        with (
            patch("src.client.connection.SESSION_DIR", tmp_path),
            patch("src.client.connection.time.time", return_value=now),
        ):
            deleted = await _cleanup_inactive_sessions()

        assert deleted == 1
        assert not session_file.is_file()  # removed

    @pytest.mark.asyncio
    async def test_keeps_session_when_last_active_recent(self, tmp_path):
        """Session with last_active <30 days ago is kept."""
        now = 1_000_000_000.0
        recent_time = now - 1  # 1 second ago

        token = _TOKEN
        tracking = {
            token: {
                "last_active": recent_time,
                "failure_count": 0,
                "last_failure": None,
            }
        }
        tracking_path = tmp_path / "session_tracking.json"
        tracking_path.write_text(json.dumps(tracking), encoding="utf-8")
        session_file = make_session_file(tmp_path, token)

        with (
            patch("src.client.connection.SESSION_DIR", tmp_path),
            patch("src.client.connection.time.time", return_value=now),
        ):
            deleted = await _cleanup_inactive_sessions()

        assert deleted == 0
        assert session_file.is_file()  # still there

    @pytest.mark.asyncio
    async def test_falls_back_to_file_mtime_when_last_active_is_none(self, tmp_path):
        """Token with last_active=None checks file mtime.

        If mtime is recent, session is kept.
        """
        now = 1_000_000_000.0

        token = _TOKEN
        tracking = {
            token: {"last_active": None, "failure_count": 0, "last_failure": None}
        }
        tracking_path = tmp_path / "session_tracking.json"
        tracking_path.write_text(json.dumps(tracking), encoding="utf-8")
        session_file = make_session_file(tmp_path, token)
        # Touch file to have recent mtime
        session_file.touch()

        with (
            patch("src.client.connection.SESSION_DIR", tmp_path),
            patch("src.client.connection.time.time", return_value=now),
        ):
            deleted = await _cleanup_inactive_sessions()

        assert deleted == 0, "Should keep session with recent mtime"
        assert session_file.is_file()

    @pytest.mark.asyncio
    async def test_deletes_when_no_last_active_and_old_mtime(self, tmp_path):
        """Token with last_active=None and old file mtime is deleted."""
        now = 1_000_000_000.0
        cutoff_secs = _INACTIVE_SESSION_DAYS * 86400
        old_mtime = now - cutoff_secs - 1000

        token = _TOKEN
        tracking = {
            token: {"last_active": None, "failure_count": 0, "last_failure": None}
        }
        tracking_path = tmp_path / "session_tracking.json"
        tracking_path.write_text(json.dumps(tracking), encoding="utf-8")
        session_file = make_session_file(tmp_path, token)

        # Set old mtime using os.utime to avoid patching Path.stat
        import os as _os

        _os.utime(str(session_file), (old_mtime, old_mtime))

        with (
            patch("src.client.connection.SESSION_DIR", tmp_path),
            patch("src.client.connection.time.time", return_value=now),
        ):
            deleted = await _cleanup_inactive_sessions()

        assert deleted == 1
        assert not session_file.is_file()

    @pytest.mark.asyncio
    async def test_mixed_tokens_active_and_inactive(self, tmp_path):
        """Multiple tokens: some kept, some deleted."""
        now = 1_000_000_000.0
        cutoff_secs = _INACTIVE_SESSION_DAYS * 86400

        active_token = _TOKEN
        inactive_token = _TOKEN_B

        tracking = {
            active_token: {
                "last_active": now - 10,
                "failure_count": 0,
                "last_failure": None,
            },  # 10s ago
            inactive_token: {
                "last_active": now - cutoff_secs - 500,
                "failure_count": 5,
                "last_failure": None,
            },  # well before cutoff
        }
        tracking_path = tmp_path / "session_tracking.json"
        tracking_path.write_text(json.dumps(tracking), encoding="utf-8")

        active_file = make_session_file(tmp_path, active_token)
        inactive_file = make_session_file(tmp_path, inactive_token)

        with (
            patch("src.client.connection.SESSION_DIR", tmp_path),
            patch("src.client.connection.time.time", return_value=now),
        ):
            deleted = await _cleanup_inactive_sessions()

        assert deleted == 1
        assert active_file.is_file()  # kept
        assert not inactive_file.is_file()  # deleted


# ---------------------------------------------------------------------------
# _record_connection_failure — disk persistence
# ---------------------------------------------------------------------------


class TestRecordConnectionFailurePersistence:
    """_record_connection_failure also persists to the tracking file."""

    @pytest.mark.asyncio
    async def test_persists_failure_count_to_disk(self, tmp_path, tracking_path):
        """Failure count and timestamp are written to disk."""
        token = "tok_fail"
        # Pre-populate tracking entry
        tracking = {
            token: {"last_active": None, "failure_count": 0, "last_failure": None}
        }
        tracking_path.write_text(json.dumps(tracking), encoding="utf-8")

        # Mock both thread/lock objects for the asyncio context
        with (
            patch("src.client.connection.SESSION_DIR", tmp_path),
            patch("src.client.connection._failure_lock"),
            patch("src.client.connection._connection_failures", {token: (0, 0.0)}),
        ):
            await _record_connection_failure(token)

        loaded = json.loads(tracking_path.read_text(encoding="utf-8"))
        assert loaded[token]["failure_count"] == 1
        assert loaded[token]["last_failure"] is not None

    @pytest.mark.asyncio
    async def test_creates_tracking_entry_when_missing(self, tmp_path, tracking_path):
        """Token not yet in tracking file gets a new entry."""
        token = "tok_new_fail"
        with (
            patch("src.client.connection.SESSION_DIR", tmp_path),
            patch("src.client.connection._failure_lock"),
            patch("src.client.connection._connection_failures", {token: (0, 0.0)}),
        ):
            await _record_connection_failure(token)

        loaded = json.loads(tracking_path.read_text(encoding="utf-8"))
        assert loaded[token]["failure_count"] == 1
        assert loaded[token]["last_active"] is None
        assert loaded[token]["last_failure"] is not None


# ---------------------------------------------------------------------------
# Tracking file — round trip
# ---------------------------------------------------------------------------


class TestTrackingRoundTrip:
    """Disk persistence round trip for tracking data."""

    @pytest.mark.asyncio
    async def test_save_and_load_preserves_data(self, tmp_path, tracking_path):
        """Data saved via _save_session_tracking is readable by _load_session_tracking."""
        data = {
            "tok_a": {"last_active": 100.0, "failure_count": 2, "last_failure": 95.0},
            "tok_b": {"last_active": 200.0, "failure_count": 0, "last_failure": None},
        }
        with patch("src.client.connection.SESSION_DIR", tmp_path):
            _save_session_tracking(data)
            loaded = _load_session_tracking()
        assert loaded == data
