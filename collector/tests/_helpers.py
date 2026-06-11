"""Test helper functions shared across the collector test suite."""

from __future__ import annotations

from datetime import UTC, datetime


def make_nested_payload(**overrides) -> dict:
    """Build a payload that mirrors ``src.telemetry.gather_payload()`` output.

    Returns a dict that passes the collector's ``TelemetryPayload``
    validation. Tests can override any field via keyword args.
    """
    now = int(datetime.now(UTC).timestamp())
    payload = {
        "v": 1,
        "iid": "550e8400-e29b-41d4-a716-446655440000",
        "ts": now,
        "started_at": now,
        "ver": "0.7.0",
        "os": "linux x86_64",
        "py": "3.12",
        "features": {"raw_edit": True, "sandboxed": False},
        "runtime": {"sessions": 0, "session_files": 0, "setup_sessions": 0},
        "counters": {"total_calls": 0, "errors": 0},
    }
    payload.update(overrides)
    return payload
