#!/usr/bin/env python3
"""Tests for 64-bit id JSON serialization.

Covers:
- ``stringify_int64`` / ``id_to_str`` / ``sanitize_int64_tree`` helpers
- lossless round-trip of real Telegram int64 ids through json.dumps/loads
- ``_json_safe`` (invoke_mtproto path) stringifying out-of-range ints in
  nested TL ``to_dict()`` trees
- ``build_entity_dict`` emitting id/access_hash as strings and explicit
  ``is_supergroup``/``megagroup``/``is_broadcast``/``is_forum`` flags
"""

import json
from types import SimpleNamespace

from src.tools.mtproto import _json_safe
from src.utils.entity import _ENTITY_DICT_CACHE, _ENTITY_TYPE_CACHE, build_entity_dict
from src.utils.json_ids import (
    JS_SAFE_MAX,
    id_to_str,
    sanitize_int64_tree,
    stringify_int64,
)

# Real Telegram custom-emoji document_ids (and a couple of synthetic edge values)
# that all exceed 2**53 and lose precision when JSON-decoded as doubles.
REAL_INT64_IDS = [
    5366182128746793135,
    5314748207455037000,
    5411225014148014000,
    9007199254740993,  # 2**53 + 1: smallest int a double cannot represent exactly
]


class TestStringifyInt64:
    def test_large_ints_become_strings(self):
        for value in REAL_INT64_IDS:
            assert stringify_int64(value) == str(value)

    def test_js_safe_ints_pass_through(self):
        assert stringify_int64(JS_SAFE_MAX) == JS_SAFE_MAX
        assert stringify_int64(123) == 123
        assert stringify_int64(0) == 0
        assert stringify_int64(-42) == -42

    def test_large_negative_int_becomes_string(self):
        # Channel peer ids are large negatives (e.g. -100<id>).
        assert stringify_int64(-1003419925550) == -1003419925550  # within JS-safe
        big_neg = -(2**60)
        assert stringify_int64(big_neg) == str(big_neg)

    def test_bool_and_non_int_pass_through(self):
        assert stringify_int64(True) is True
        assert stringify_int64(False) is False
        assert stringify_int64("abc") == "abc"
        assert stringify_int64(None) is None
        assert stringify_int64(1.5) == 1.5


class TestIdToStr:
    def test_any_int_becomes_string(self):
        assert id_to_str(123) == "123"
        assert id_to_str(5366182128746793135) == "5366182128746793135"

    def test_none_and_str_preserved(self):
        assert id_to_str(None) is None
        assert id_to_str("already") == "already"

    def test_bool_preserved(self):
        assert id_to_str(True) is True


class TestRoundTrip:
    def test_lossless_round_trip_through_json(self):
        """str(int64) survives json.dumps/loads with every digit intact."""
        for value in REAL_INT64_IDS:
            payload = {"document_id": id_to_str(value)}
            decoded = json.loads(json.dumps(payload))
            assert decoded["document_id"] == str(value)
            assert int(decoded["document_id"]) == value

    def test_raw_int_would_lose_precision(self):
        """Guard: raw int64 round-tripped via float (JS behavior) is lossy."""
        value = 5366182128746793135
        lossy = int(float(value))
        assert lossy != value  # demonstrates why we stringify


class TestSanitizeTree:
    def test_nested_tree_stringifies_out_of_range_only(self):
        tree = {
            "_": "MessageEntityCustomEmoji",
            "offset": 0,
            "length": 5,
            "document_id": 5366182128746793135,
            "nested": [{"access_hash": 9007199254740993, "flags": 16}],
        }
        out = sanitize_int64_tree(tree)
        assert out["document_id"] == "5366182128746793135"
        assert out["nested"][0]["access_hash"] == "9007199254740993"
        # Small ints stay numeric
        assert out["offset"] == 0
        assert out["length"] == 5
        assert out["nested"][0]["flags"] == 16


class TestJsonSafeMtproto:
    def test_json_safe_stringifies_large_int(self):
        assert _json_safe(5366182128746793135) == "5366182128746793135"

    def test_json_safe_preserves_small_int_and_bool(self):
        assert _json_safe(16) == 16
        assert _json_safe(True) is True

    def test_json_safe_recurses_into_dict_with_document_id(self):
        tl = {
            "_": "DocumentEmpty",
            "document_id": 5314748207455037000,
            "date": 1700000000,
        }
        out = _json_safe(tl)
        assert out["document_id"] == "5314748207455037000"
        assert out["date"] == 1700000000


class TestBuildEntityDict:
    def setup_method(self):
        _ENTITY_DICT_CACHE.clear()
        _ENTITY_TYPE_CACHE.clear()

    def _channel(self, **kwargs):
        # A class literally named "Channel" so get_normalized_chat_type resolves it.
        return type("Channel", (), {})(), kwargs

    def test_ids_are_strings(self):
        entity = SimpleNamespace(
            id=5314748207455037000,
            access_hash=9007199254740993,
            title="Big",
        )
        result = build_entity_dict(entity)
        assert result["id"] == "5314748207455037000"
        assert result["access_hash"] == "9007199254740993"
        assert isinstance(result["id"], str)
        assert isinstance(result["access_hash"], str)

    def test_megagroup_flags_explicit(self):
        chan = type("Channel", (), {})()
        chan.id = 1234567890
        chan.access_hash = 111
        chan.title = "Supergroup"
        chan.megagroup = True
        chan.broadcast = False
        chan.forum = True
        result = build_entity_dict(chan)
        assert result["is_supergroup"] is True
        assert result["megagroup"] is True
        assert result["is_forum"] is True
        assert "is_broadcast" not in result  # pruned when False
        # A megagroup normalizes to type "group" — exactly why explicit
        # is_supergroup/megagroup flags are needed to disambiguate.
        assert result["type"] == "group"

    def test_broadcast_channel_flags(self):
        chan = type("Channel", (), {})()
        chan.id = 222
        chan.access_hash = 333
        chan.title = "News"
        chan.megagroup = False
        chan.broadcast = True
        result = build_entity_dict(chan)
        assert result["is_broadcast"] is True
        assert "is_supergroup" not in result
        assert "megagroup" not in result
