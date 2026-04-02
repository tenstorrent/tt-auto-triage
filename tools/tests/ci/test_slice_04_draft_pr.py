"""Contract tests for Slice 4: stale thread selection and disable-action execution.

Validates that select_stale_unresolved_threads filters correctly and
execute_disable_actions state management contracts hold.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.ci.execute_disable_actions import (
    ALLOWED_STATUS,
    ensure_state_item,
    load_state,
    save_state,
    set_status,
    state_index,
    state_key_for_ts,
)
from tools.ci.select_stale_unresolved_threads import (
    issue_numbers,
    message_replies,
    permalink,
    primary_issue_detail,
)


def test_state_round_trip(tmp_path):
    """State can be saved and reloaded with schema=disable validation."""
    path = tmp_path / "state.json"
    from tools.ci.common.state import empty_state

    state = empty_state()
    state["items"].append({"key": "slack_ts:1.0", "status": "new", "attempts": 0})
    save_state(path, state)
    loaded = load_state(path)
    assert len(loaded["items"]) == 1
    assert loaded["items"][0]["key"] == "slack_ts:1.0"


def test_ensure_state_item_creates_new_entry():
    from tools.ci.common.state import empty_state

    state = empty_state()
    action = {"source_slack_ts": "123.456", "issue_number": 42}
    item = ensure_state_item(state, action)
    assert item["key"] == state_key_for_ts("123.456")
    assert item["status"] == "new"
    assert 42 in item["issue_numbers"]


def test_ensure_state_item_returns_existing():
    from tools.ci.common.state import empty_state

    state = empty_state()
    action = {"source_slack_ts": "1.0", "issue_number": 1}
    first = ensure_state_item(state, action)
    second = ensure_state_item(state, action)
    assert first is second
    assert len(state["items"]) == 1


def test_set_status_transitions():
    item = {"status": "new", "history": []}
    set_status(item, "planned", event="test", details="reason")
    assert item["status"] == "planned"
    assert len(item["history"]) == 1


def test_permalink_format():
    assert permalink("C123", "1234567890.123456") == (
        "https://tenstorrent.slack.com/archives/C123/p1234567890123456"
    )


def test_issue_numbers_extracts_ints():
    msg = {"referenced_issue_numbers": [42, "99"]}
    assert issue_numbers(msg) == [42, 99]


def test_primary_issue_detail_returns_first():
    msg = {"referenced_issues": [{"repo": "org/repo", "number": 1}]}
    detail = primary_issue_detail(msg)
    assert detail is not None
    assert detail["number"] == 1


def test_message_replies_handles_both_keys():
    assert message_replies({"thread_replies": [{"ts": "1"}]}) == [{"ts": "1"}]
    assert message_replies({"replies": [{"ts": "2"}]}) == [{"ts": "2"}]
    assert message_replies({}) == []
