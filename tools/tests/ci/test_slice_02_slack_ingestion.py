"""Contract tests for Slice 2: Slack data ingestion pipeline.

Validates that export_slack_channel, export_slack_author_threads, and
enrich_slack_with_issue_status correctly consume Slack/GitHub API data
and produce the expected output schemas.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.ci.export_slack_channel import fetch_channel_messages, fetch_thread_replies
from tools.ci.export_slack_author_threads import is_top_level
from tools.ci.enrich_slack_with_issue_status import ISSUE_URL_RE, SUPPORTED_ISSUE_REPOS


def test_fetch_channel_messages_paginates(monkeypatch):
    """Verify fetch_channel_messages follows cursor pagination."""
    page1 = {
        "ok": True,
        "messages": [{"ts": "1.0", "text": "hello"}],
        "response_metadata": {"next_cursor": "cursor_abc"},
    }
    page2 = {
        "ok": True,
        "messages": [{"ts": "2.0", "text": "world"}],
        "response_metadata": {"next_cursor": ""},
    }
    call_count = {"n": 0}

    def mock_get(token, endpoint, params, *, max_retries=5):
        call_count["n"] += 1
        return page1 if call_count["n"] == 1 else page2

    with patch("tools.ci.export_slack_channel.slack_api_get", side_effect=mock_get):
        msgs = fetch_channel_messages("tok", "C123", 0.0, 999.0)

    assert len(msgs) == 2
    assert msgs[0]["ts"] == "1.0"
    assert msgs[1]["ts"] == "2.0"


def test_fetch_thread_replies_excludes_root(monkeypatch):
    """Verify fetch_thread_replies filters out the root message."""
    payload = {
        "ok": True,
        "messages": [
            {"ts": "1.0", "text": "root"},
            {"ts": "1.1", "text": "reply"},
        ],
        "response_metadata": {},
    }

    with patch("tools.ci.export_slack_channel.slack_api_get", return_value=payload):
        replies = fetch_thread_replies("tok", "C123", "1.0", 0.0, 999.0)

    assert len(replies) == 1
    assert replies[0]["ts"] == "1.1"


def test_is_top_level_detects_root_vs_reply():
    assert is_top_level({"ts": "1.0", "thread_ts": "1.0"}) is True
    assert is_top_level({"ts": "1.0"}) is True
    assert is_top_level({"ts": "1.1", "thread_ts": "1.0"}) is False


def test_issue_url_regex_captures_supported_repos():
    text = "See https://github.com/tenstorrent/tt-metal/issues/42 for details"
    matches = ISSUE_URL_RE.findall(text)
    assert len(matches) == 1
    owner, repo, num = matches[0]
    assert f"{owner}/{repo}" in SUPPORTED_ISSUE_REPOS
    assert num == "42"


def test_enrichment_output_schema(tmp_path):
    """Verify enrich_slack_with_issue_status produces expected fields on messages."""
    from tools.ci.enrich_slack_with_issue_status import main as enrich_main

    input_data = {
        "messages": [
            {"ts": "1.0", "text": "No issues here"},
            {"ts": "2.0", "text": "Bug https://github.com/tenstorrent/tt-metal/issues/99"},
        ]
    }
    inp = tmp_path / "input.json"
    out = tmp_path / "output.json"
    inp.write_text(json.dumps(input_data))

    with patch("tools.ci.enrich_slack_with_issue_status.fetch_issue", return_value={"state": "open", "html_url": "u", "title": "t"}):
        import sys

        old_argv = sys.argv
        sys.argv = ["enrich", "--input", str(inp), "--output", str(out)]
        try:
            enrich_main()
        finally:
            sys.argv = old_argv

    result = json.loads(out.read_text())
    for msg in result["messages"]:
        assert "referenced_issue_numbers" in msg
        assert "issue_closed" in msg
        assert "all_referenced_issues_closed" in msg
