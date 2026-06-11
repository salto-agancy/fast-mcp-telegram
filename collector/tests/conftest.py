"""Test fixtures for the telemetry collector.

The fixtures use the nested payload format produced by
``src.telemetry.gather_payload()`` — this is the real format the
client sends. Tests verify that the collector accepts it.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from collector.app.models import TelemetryPayload
from collector.tests._helpers import make_nested_payload


class InMemoryStorage:
    """In-memory storage backend for testing."""

    def __init__(self):
        self.events: list[dict] = []

    async def store(
        self,
        payload: TelemetryPayload,
        source_ip_hash: str,
        payload_hash: str,
    ) -> None:
        self.events.append({
            "payload": payload,
            "source_ip_hash": source_ip_hash,
            "received_at": datetime.now(UTC),
            "payload_hash": payload_hash,
        })

    async def count_recent_events(
        self, instance_id: str, window_hours: int = 24
    ) -> int:
        cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
        return sum(
            1 for e in self.events
            if e["payload"].iid == instance_id
            and e["received_at"] >= cutoff
        )

    async def has_exact_payload(
        self, payload_hash: str, window_seconds: int = 300
    ) -> bool:
        cutoff = datetime.now(UTC) - timedelta(seconds=window_seconds)
        return any(
            e["payload_hash"] == payload_hash and e["received_at"] >= cutoff
            for e in self.events
        )

    async def enforce_row_cap(self, max_rows: int = 10_000_000) -> int:
        if len(self.events) <= max_rows:
            return 0
        # Delete oldest events (mimic the SQL ORDER BY id DESC + OFFSET
        # behaviour, which keeps the newest and drops the oldest).
        keep = self.events[-max_rows:]
        removed = len(self.events) - len(keep)
        self.events = keep
        return removed

    async def cleanup_ttl(self, retention_days: int = 90) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        before = len(self.events)
        self.events = [
            e for e in self.events if e["received_at"] >= cutoff
        ]
        return before - len(self.events)

    async def close(self) -> None:
        self.events.clear()


@pytest.fixture
def storage():
    """In-memory storage backend for tests."""
    return InMemoryStorage()


@pytest.fixture
def valid_payload_data():
    """A valid telemetry payload as a dict (matches gather_payload)."""
    return make_nested_payload()


@pytest.fixture
def app_with_storage(storage):
    """Build the FastAPI app wired to an in-memory storage."""
    from collector.app.main import create_app
    return create_app(storage_backend=storage)


@pytest.fixture
def client(app_with_storage):
    """FastAPI TestClient."""
    with TestClient(app_with_storage) as c:
        yield c
