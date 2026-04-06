"""Tests for tools.ci.common.state."""

from __future__ import annotations

import json

import pytest

from tools.ci.common.state import (
    ALLOWED_STATUS,
    append_history,
    empty_state,
    load_state,
    save_state,
)


def test_empty_state_schema():
    s = empty_state()
    assert s["version"] == 1
    assert isinstance(s["items"], list)
    assert "updated_at_utc" in s


def test_save_and_load_base(tmp_path):
    path = tmp_path / "state.json"
    state = empty_state()
    state["items"].append({"key": "test", "status": "new"})
    save_state(path, state)
    loaded = load_state(path, schema="base")
    assert loaded["items"][0]["key"] == "test"


def test_load_base_missing_file(tmp_path):
    s = load_state(tmp_path / "missing.json", schema="base")
    assert s == empty_state() or s["version"] == 1


def test_load_issue_lifecycle_creates_nested(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": 1, "items": []}))
    s = load_state(path, schema="issue_lifecycle")
    assert isinstance(s["issue_lifecycle"]["issues"], dict)


def test_load_issue_lifecycle_missing_file(tmp_path):
    s = load_state(tmp_path / "nope.json", schema="issue_lifecycle")
    assert "issue_lifecycle" in s
    assert isinstance(s["issue_lifecycle"]["issues"], dict)


def test_load_disable_valid(tmp_path):
    path = tmp_path / "state.json"
    data = {
        "version": 1,
        "updated_at_utc": "2024-01-01T00:00:00Z",
        "items": [{"key": "a", "status": "new", "attempts": 0}],
    }
    path.write_text(json.dumps(data))
    loaded = load_state(path, schema="disable")
    assert loaded["items"][0]["key"] == "a"


def test_load_disable_duplicate_key_raises(tmp_path):
    path = tmp_path / "state.json"
    data = {
        "version": 1,
        "items": [
            {"key": "a", "status": "new", "attempts": 0},
            {"key": "a", "status": "planned", "attempts": 0},
        ],
    }
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="duplicate state key"):
        load_state(path, schema="disable")


def test_load_disable_bad_status_raises(tmp_path):
    path = tmp_path / "state.json"
    data = {"version": 1, "items": [{"key": "a", "status": "bogus", "attempts": 0}]}
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="invalid state status"):
        load_state(path, schema="disable")


def test_load_unknown_schema_raises(tmp_path):
    with pytest.raises(ValueError, match="unknown state schema"):
        load_state(tmp_path / "x.json", schema="bogus")


def test_append_history():
    item: dict = {"key": "test"}
    append_history(item, "created", "initial")
    assert len(item["history"]) == 1
    assert item["history"][0]["event"] == "created"
    assert "ts_utc" in item["history"][0]


def test_allowed_status_has_expected_values():
    for s in ("new", "planned", "completed", "needs_human"):
        assert s in ALLOWED_STATUS
