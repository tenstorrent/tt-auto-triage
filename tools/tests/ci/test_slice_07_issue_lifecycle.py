"""Contract tests for Slice 7: issue lifecycle stage transitions.

Validates state loading with issue_lifecycle schema, lifecycle
decision parsing, and save_state round-trips.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.ci.issue_lifecycle_stage import (
    FINAL_MARKER,
    RUN_JOB_URL_RE,
    load_state,
    save_state,
)
from tools.ci.common.markers import parse_json_after_marker
from tools.ci.common.timestamps import now_iso, parse_iso_utc


def test_load_state_creates_issue_lifecycle_key(tmp_path):
    path = tmp_path / "state.json"
    state = load_state(path)
    assert "issue_lifecycle" in state
    assert isinstance(state["issue_lifecycle"]["issues"], dict)


def test_load_state_preserves_existing_issues(tmp_path):
    path = tmp_path / "state.json"
    data = {
        "version": 1,
        "items": [],
        "issue_lifecycle": {"issues": {"42": {"status": "open"}}},
    }
    path.write_text(json.dumps(data))
    state = load_state(path)
    assert "42" in state["issue_lifecycle"]["issues"]


def test_save_state_updates_timestamp(tmp_path):
    path = tmp_path / "state.json"
    state = {"version": 1, "items": [], "issue_lifecycle": {"issues": {}}}
    save_state(path, state)
    loaded = json.loads(path.read_text())
    assert "updated_at_utc" in loaded
    ts = parse_iso_utc(loaded["updated_at_utc"])
    assert ts is not None


def test_run_job_url_regex_captures_run_and_job():
    url = "https://github.com/tenstorrent/tt-metal/actions/runs/12345/job/67890"
    m = RUN_JOB_URL_RE.match(url)
    assert m is not None
    assert m.group(1) == "12345"
    assert m.group(2) == "67890"


def test_parse_lifecycle_decision_marker():
    text = f"preamble\n{FINAL_MARKER}\n" + json.dumps({
        "action": "close",
        "reason": "issue resolved",
    })
    result = parse_json_after_marker(text, FINAL_MARKER)
    assert result["action"] == "close"
    assert result["reason"] == "issue resolved"


def test_now_iso_format_matches_z_suffix():
    ts = now_iso()
    assert ts.endswith("Z")
    assert parse_iso_utc(ts) is not None
