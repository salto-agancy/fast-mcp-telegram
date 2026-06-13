"""Payload model for the telemetry collector.

Replaced Pydantic v2 with stdlib ``dataclasses`` to reduce RSS
(~15-20 MB saved by not loading pydantic-core Rust .so).

Validation runs in ``__post_init__`` — no type coercion, strict
type checks.  All errors are collected and reported at once.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

# Maximum allowed heartbeat timestamp drift in seconds.  The collector
# rejects heartbeats whose ``ts`` is more than this many seconds in the
# future (clock-skew tolerance) or older than the rejection window.
_FUTURE_DRIFT_SECONDS = 300  # 5 min
_OLD_WINDOW_SECONDS = 7 * 24 * 3600  # 7 days


class ValidationError(Exception):
    """The payload failed schema or business-rule validation."""


@dataclass
class TelemetryPayload:
    """Schema for incoming anonymous telemetry events.

    Matches the payload sent by fast-mcp-telegram library clients
    (see ``src.telemetry.gather_payload`` and ADR 0005).

    No extra fields are allowed — use ``.from_dict()`` for construction
    (it checks for unknown keys).
    """

    v: Literal[1]
    iid: str
    ts: int
    started_at: int
    ver: str
    os: str
    py: str
    features: dict[str, Any] = field(default_factory=dict)
    runtime: dict[str, int] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    tools: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate all field constraints.

        ``Literal[1]`` on ``v`` is not enforced by ``dataclass`` init —
        Python does not check type annotations at runtime — so we
        validate it manually along with length bounds and non-negativity.
        """
        errors: list[str] = []

        # --- v (Literal[1]) ---
        if self.v != 1:
            errors.append(f"v must be 1, got {self.v!r}")

        # --- iid ---
        if not isinstance(self.iid, str) or not self.iid:
            errors.append("iid must be a non-empty string")
        elif len(self.iid) > 128:
            errors.append(f"iid exceeds 128 chars ({len(self.iid)})")

        # --- ts ---
        if not isinstance(self.ts, int) or isinstance(self.ts, bool):
            errors.append("ts must be an integer")
        else:
            now = int(time.time())
            if self.ts > now + _FUTURE_DRIFT_SECONDS:
                errors.append(
                    f"ts {self.ts} is {self.ts - now}s in the future "
                    f"(max {_FUTURE_DRIFT_SECONDS}s)"
                )
            if self.ts < now - _OLD_WINDOW_SECONDS:
                errors.append(
                    f"ts {self.ts} is {now - self.ts}s old (max {_OLD_WINDOW_SECONDS}s)"
                )

        # --- started_at ---
        if not isinstance(self.started_at, int) or isinstance(self.started_at, bool):
            errors.append("started_at must be an integer")
        elif self.started_at < 0:
            errors.append(f"started_at must be >= 0, got {self.started_at}")

        # --- ver ---
        if not isinstance(self.ver, str) or not self.ver:
            errors.append("ver must be a non-empty string")
        elif len(self.ver) > 64:
            errors.append(f"ver exceeds 64 chars ({len(self.ver)})")

        # --- os ---
        if not isinstance(self.os, str):
            errors.append("os must be a string")
        elif len(self.os) > 128:
            errors.append(f"os exceeds 128 chars ({len(self.os)})")

        # --- py ---
        if not isinstance(self.py, str):
            errors.append("py must be a string")
        elif len(self.py) > 32:
            errors.append(f"py exceeds 32 chars ({len(self.py)})")

        # --- features ---
        if not isinstance(self.features, dict):
            errors.append("features must be a dict")
        else:
            if len(self.features) > 256:
                errors.append(f"features has {len(self.features)} keys (max 256)")
            for k in self.features:
                if not isinstance(k, str) or not k:
                    errors.append("feature keys must be non-empty strings")
                    break
                if len(k) > 128:
                    errors.append(f"feature key {k!r} exceeds 128 chars")
                    break

        # --- runtime / counters (dict[str, int] with non-negative values) ---
        for field_name, d in (("runtime", self.runtime), ("counters", self.counters)):
            if not isinstance(d, dict):
                errors.append(f"{field_name} must be a dict")
                continue
            for k, val in d.items():
                if not isinstance(val, int) or isinstance(val, bool):
                    errors.append(
                        f"{field_name}.{k!r} must be int, got {type(val).__name__}"
                    )
                    break
                if val < 0:
                    errors.append(f"{field_name}.{k!r} must be >= 0, got {val}")
                    break

        if errors:
            raise ValidationError("; ".join(errors))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict.

        Replaces ``pydantic.BaseModel.model_dump(mode="json")``.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TelemetryPayload:
        """Construct and validate from a raw dict.

        Rejects extra keys not in the schema (like pydantic's
        ``model_config = {"extra": "forbid"}``).

        Raises ``ValidationError`` on any issue.
        """
        known = set(cls.__dataclass_fields__)
        extra = set(data) - known
        if extra:
            raise ValidationError(f"Unexpected fields: {', '.join(sorted(extra))}")
        try:
            return cls(**data)
        except TypeError as exc:
            raise ValidationError(str(exc)) from exc
