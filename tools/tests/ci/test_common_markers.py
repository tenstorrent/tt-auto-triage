"""Tests for tools.ci.common.markers."""

from __future__ import annotations

import pytest

from tools.ci.common.markers import (
    parse_agent_json_after_marker,
    parse_agent_json_payload,
    parse_json_after_marker,
    strip_json_fence,
)


class TestParseJsonAfterMarker:
    def test_simple_success(self):
        text = "preamble ===MARKER=== {\"key\": \"value\"}"
        result = parse_json_after_marker(text, "===MARKER===")
        assert result == {"key": "value"}

    def test_missing_marker_raises(self):
        with pytest.raises(ValueError, match="marker not found"):
            parse_json_after_marker("no marker here", "===MISSING===")

    def test_empty_payload_raises(self):
        with pytest.raises(ValueError, match="empty json payload"):
            parse_json_after_marker("prefix ===M===  ", "===M===")

    def test_non_object_raises(self):
        with pytest.raises(ValueError, match="not a JSON object"):
            parse_json_after_marker('===M=== ["array"]', "===M===")


class TestStripJsonFence:
    def test_removes_backtick_fence(self):
        assert strip_json_fence('```json\n{"a":1}\n```') == '{"a":1}'

    def test_no_fence_passes_through(self):
        assert strip_json_fence('{"a":1}') == '{"a":1}'


class TestParseAgentJsonPayload:
    def test_with_marker(self):
        text = "stuff ===M=== {\"ok\": true}"
        result = parse_agent_json_payload(text, marker="===M===")
        assert result == {"ok": True}

    def test_fallback_raw_json(self):
        text = '{"fallback": true}'
        result = parse_agent_json_payload(text, marker="===MISSING===")
        assert result == {"fallback": True}

    def test_fallback_fenced_block(self):
        text = 'some text\n```json\n{"fenced": true}\n```'
        result = parse_agent_json_payload(text, marker="===MISSING===")
        assert result == {"fenced": True}

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="output excerpt: <empty>"):
            parse_agent_json_payload("", marker="===M===")

    def test_unparseable_raises(self):
        with pytest.raises(ValueError, match="Could not parse fallback"):
            parse_agent_json_payload("just plain text here", marker="===M===")


class TestParseAgentJsonAfterMarker:
    def test_with_marker(self):
        text = "output ===M=== {\"status\": \"ok\"}"
        result = parse_agent_json_after_marker(text, "===M===")
        assert result == {"status": "ok"}

    def test_fenced_payload(self):
        text = "output ===M===\n```json\n{\"fenced\": true}\n```"
        result = parse_agent_json_after_marker(text, "===M===")
        assert result == {"fenced": True}

    def test_backtick_wrapped_marker(self):
        text = "output `===M===` {\"wrapped\": true}"
        result = parse_agent_json_after_marker(text, "===M===")
        assert result == {"wrapped": True}

    def test_missing_marker_raises(self):
        with pytest.raises(ValueError, match="marker not found"):
            parse_agent_json_after_marker("no marker", "===MISSING===")

    def test_brace_fallback(self):
        text = "output ===M=== some junk {\"found\": true}"
        result = parse_agent_json_after_marker(text, "===M===")
        assert result == {"found": True}
