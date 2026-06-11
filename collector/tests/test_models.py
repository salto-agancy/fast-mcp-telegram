"""Tests for the telemetry payload Pydantic models."""

import time

import pytest
from pydantic import ValidationError

from app.models import TelemetryPayload


class TestTelemetryPayload:
    """Schema validation for incoming telemetry events."""

    def test_valid_payload(self, valid_payload_data):
        """A well-formed payload passes validation."""
        payload = TelemetryPayload(**valid_payload_data)
        assert payload.iid == valid_payload_data["iid"]
        assert payload.v == 1
        assert payload.counters["total_calls"] == 0
        assert payload.counters["errors"] == 0
        assert payload.features["raw_edit"] is True

    def test_missing_required_field_fails(self, valid_payload_data):
        """Omitting a required field raises ValidationError."""
        del valid_payload_data["iid"]
        with pytest.raises(ValidationError):
            TelemetryPayload(**valid_payload_data)

    def test_extra_field_fails(self, valid_payload_data):
        """extra='forbid' rejects unknown fields."""
        valid_payload_data["hacked"] = "yes"
        with pytest.raises(ValidationError):
            TelemetryPayload(**valid_payload_data)

    def test_v_must_be_one(self, valid_payload_data):
        """Schema version must be 1 (the only supported version)."""
        valid_payload_data["v"] = 2
        with pytest.raises(ValidationError):
            TelemetryPayload(**valid_payload_data)

    def test_negative_counter_fails(self, valid_payload_data):
        """Counter values must be non-negative integers."""
        valid_payload_data["counters"] = {"total_calls": -5, "errors": 0}
        with pytest.raises(ValidationError):
            TelemetryPayload(**valid_payload_data)

    def test_runtime_must_be_int_values(self, valid_payload_data):
        """Runtime values must be integers."""
        valid_payload_data["runtime"] = {"sessions": "five"}
        with pytest.raises(ValidationError):
            TelemetryPayload(**valid_payload_data)

    def test_future_ts_beyond_5min_fails(self, valid_payload_data):
        """``ts`` more than 5 min in the future is rejected."""
        valid_payload_data["ts"] = int(time.time()) + 600
        with pytest.raises(ValidationError):
            TelemetryPayload(**valid_payload_data)

    def test_old_ts_beyond_7days_fails(self, valid_payload_data):
        """``ts`` older than 7 days is rejected."""
        valid_payload_data["ts"] = int(time.time()) - (8 * 86400)
        with pytest.raises(ValidationError):
            TelemetryPayload(**valid_payload_data)

    def test_old_started_at_is_accepted(self, valid_payload_data):
        """``started_at`` (process boot) may be much older than ``ts``."""
        valid_payload_data["started_at"] = int(time.time()) - (90 * 86400)
        payload = TelemetryPayload(**valid_payload_data)
        assert payload.started_at < payload.ts

    def test_features_defaults_to_empty(self, valid_payload_data):
        """features is optional and defaults to empty dict."""
        del valid_payload_data["features"]
        payload = TelemetryPayload(**valid_payload_data)
        assert payload.features == {}

    def test_runtime_defaults_to_empty(self, valid_payload_data):
        """runtime is optional and defaults to empty dict."""
        del valid_payload_data["runtime"]
        payload = TelemetryPayload(**valid_payload_data)
        assert payload.runtime == {}

    def test_counters_defaults_to_empty(self, valid_payload_data):
        """counters is optional and defaults to empty dict."""
        del valid_payload_data["counters"]
        payload = TelemetryPayload(**valid_payload_data)
        assert payload.counters == {}

    def test_features_too_many_keys_fails(self, valid_payload_data):
        """Reject payloads with more than 256 feature keys."""
        valid_payload_data["features"] = {f"f{i}": True for i in range(300)}
        with pytest.raises(ValidationError):
            TelemetryPayload(**valid_payload_data)

    def test_iid_too_long_fails(self, valid_payload_data):
        """iid longer than 128 chars is rejected."""
        valid_payload_data["iid"] = "a" * 200
        with pytest.raises(ValidationError):
            TelemetryPayload(**valid_payload_data)

    def test_model_serializes_to_json(self, valid_payload_data):
        """Payload can be serialized back to a JSON-compatible dict."""
        payload = TelemetryPayload(**valid_payload_data)
        d = payload.model_dump(mode="json")
        assert d["ver"] == "0.7.0"
        assert d["features"]["raw_edit"] is True
        assert d["counters"]["total_calls"] == 0
