"""Pydantic models for the telemetry payload.

The client (fast-mcp-telegram library) sends heartbeats assembled by
``src.telemetry.gather_payload()`` — a nested document with versioning
metadata, feature flags, runtime state, and aggregate counters.

The collector validates that structure, then stores the full payload
as a JSONB column in PostgreSQL. The instance_id is also extracted to
a top-level indexed column for rate-limit queries.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# Maximum allowed heartbeat timestamp drift in seconds.  The collector
# rejects heartbeats whose ``ts`` is more than this many seconds in the
# future (clock-skew tolerance) or older than the rejection window.
_FUTURE_DRIFT_SECONDS = 300  # 5 min
_OLD_WINDOW_SECONDS = 7 * 24 * 3600  # 7 days


class TelemetryPayload(BaseModel):
    """Schema for incoming anonymous telemetry events.

    Matches the payload sent by fast-mcp-telegram library clients
    (see ``src.telemetry.gather_payload`` and ADR 0005).
    """

    model_config = {"extra": "forbid"}

    v: Literal[1] = Field(..., description="Schema version (always 1)")
    iid: str = Field(
        ..., min_length=1, max_length=128,
        description="Installation UUID generated once per install",
    )
    ts: int = Field(..., description="Unix timestamp — when this heartbeat was sent")
    started_at: int = Field(
        ..., ge=0,
        description="Unix timestamp — when the server process booted",
    )
    ver: str = Field(
        ..., min_length=1, max_length=64,
        description="Library version e.g. 0.30.1",
    )
    os: str = Field(..., max_length=128, description="OS string e.g. 'Linux x86_64'")
    py: str = Field(..., max_length=32, description="Python version e.g. '3.12'")
    features: dict[str, Any] = Field(
        default_factory=dict,
        description="Feature flags and configuration depth",
    )
    runtime: dict[str, int] = Field(
        default_factory=dict,
        description="Runtime state (sessions, file counts, etc.)",
    )
    counters: dict[str, int] = Field(
        default_factory=dict,
        description="Aggregate counters (total_calls, errors, flood_waits)",
    )

    @field_validator("ts")
    @classmethod
    def validate_ts_sanity(cls, v: int) -> int:
        """Reject ``ts`` that is far in the future or far in the past.

        ``started_at`` is intentionally NOT validated this strictly —
        it's the process boot time and may legitimately be weeks old.
        """
        now = int(time.time())
        if v > now + _FUTURE_DRIFT_SECONDS:
            raise ValueError(
                f"ts {v} is {v - now} seconds in the future (max {_FUTURE_DRIFT_SECONDS})"
            )
        if v < now - _OLD_WINDOW_SECONDS:
            raise ValueError(
                f"ts {v} is {now - v} seconds old (max {_OLD_WINDOW_SECONDS})"
            )
        return v

    @field_validator("features")
    @classmethod
    def validate_features_keys(cls, v: dict[str, Any]) -> dict[str, Any]:
        """Reject pathological feature keys (huge keys, no keys at all)."""
        if len(v) > 256:
            raise ValueError(f"features has {len(v)} keys (max 256)")
        for k in v:
            if not isinstance(k, str) or not k:
                raise ValueError("feature keys must be non-empty strings")
            if len(k) > 128:
                raise ValueError(f"feature key {k!r} exceeds 128 chars")
        return v

    @field_validator("counters", "runtime")
    @classmethod
    def validate_int_dict_nonnegative(
        cls, v: dict[str, int], info
    ) -> dict[str, int]:
        """Reject negative values in counter/runtime dicts."""
        for k, val in v.items():
            if not isinstance(val, int) or isinstance(val, bool):
                raise ValueError(
                    f"{info.field_name}.{k!r} must be int, got {type(val).__name__}"
                )
            if val < 0:
                raise ValueError(
                    f"{info.field_name}.{k!r} must be >= 0, got {val}"
                )
        return v
