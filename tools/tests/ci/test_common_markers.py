"""Tests for tools.ci.common.markers."""

from __future__ import annotations

import pytest

from tools.ci.common.markers import (
    parse_strict,
    parse_with_fallbacks,
    parse_with_regex,
    parse_json_after_marker,
    parse_agent_json_payload,
    parse_agent_json_after_marker,
    strip_json_fence,
)


class TestParseStrict:
    def test_simple_success(self):
        text = "preamble ===MARKER=== {\"key\": \"value\"}"
        result = parse_strict(text, "===MARKER===")
        assert result == {"key": "value"}

    def test_missing_marker_raises(self):
        with pytest.raises(ValueError, match="marker not found"):
            parse_strict("no marker here", "===MISSING===")

    def test_empty_payload_raises(self):
        with pytest.raises(ValueError, match="empty json payload"):
            parse_strict("prefix ===M===  ", "===M===")

    def test_non_object_raises(self):
        with pytest.raises(ValueError, match="not a JSON object"):
            parse_strict('===M=== ["array"]', "===M===")


class TestStripJsonFence:
    def test_removes_backtick_fence(self):
        assert strip_json_fence('```json\n{"a":1}\n```') == '{"a":1}'

    def test_no_fence_passes_through(self):
        assert strip_json_fence('{"a":1}') == '{"a":1}'


class TestParseWithFallbacks:
    def test_with_marker(self):
        text = "stuff ===M=== {\"ok\": true}"
        result = parse_with_fallbacks(text, marker="===M===")
        assert result == {"ok": True}

    def test_fallback_raw_json(self):
        text = '{"fallback": true}'
        result = parse_with_fallbacks(text, marker="===MISSING===")
        assert result == {"fallback": True}

    def test_fallback_fenced_block(self):
        text = 'some text\n```json\n{"fenced": true}\n```'
        result = parse_with_fallbacks(text, marker="===MISSING===")
        assert result == {"fenced": True}

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="output excerpt: <empty>"):
            parse_with_fallbacks("", marker="===M===")

    def test_unparseable_raises(self):
        with pytest.raises(ValueError, match="Could not parse fallback"):
            parse_with_fallbacks("just plain text here", marker="===M===")

    def test_brace_scan_prefers_last_match(self):
        """The brace fallback should try the last '{' first for robustness."""
        text = 'garbage {bad json} more stuff {"real": true}'
        result = parse_with_fallbacks(text, marker="===MISSING===")
        assert result == {"real": True}


class TestParseWithRegex:
    def test_with_marker(self):
        text = "output ===M=== {\"status\": \"ok\"}"
        result = parse_with_regex(text, "===M===")
        assert result == {"status": "ok"}

    def test_fenced_payload(self):
        text = "output ===M===\n```json\n{\"fenced\": true}\n```"
        result = parse_with_regex(text, "===M===")
        assert result == {"fenced": True}

    def test_backtick_wrapped_marker(self):
        text = "output `===M===` {\"wrapped\": true}"
        result = parse_with_regex(text, "===M===")
        assert result == {"wrapped": True}

    def test_missing_marker_raises(self):
        with pytest.raises(ValueError, match="marker not found"):
            parse_with_regex("no marker", "===MISSING===")

    def test_brace_fallback(self):
        text = "output ===M=== some junk {\"found\": true}"
        result = parse_with_regex(text, "===M===")
        assert result == {"found": True}


class TestLegacyAliases:
    def test_parse_json_after_marker_is_parse_strict(self):
        assert parse_json_after_marker is parse_strict

    def test_parse_agent_json_payload_is_parse_with_fallbacks(self):
        assert parse_agent_json_payload is parse_with_fallbacks

    def test_parse_agent_json_after_marker_is_parse_with_regex(self):
        assert parse_agent_json_after_marker is parse_with_regex
