"""Contract tests for Slice 5: M4 issue creation and notification.

Validates that M4 correctly processes failing jobs JSON, deduplicates
against open issues, and produces expected creation/skip records.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import tools.ci.m4_create_issues_and_notify as m4


def test_job_identity_key_is_deterministic():
    key1 = m4.job_identity_key("wf-A", "job-B")
    key2 = m4.job_identity_key("wf-A", "job-B")
    assert key1 == key2
    assert isinstance(key1, str)
    assert len(key1) > 0


def test_issue_job_identity_marker_contains_key():
    key = m4.job_identity_key("workflow", "job")
    marker = m4.issue_job_identity_marker(key)
    assert key in marker
    assert "Auto-triage-job-key" in marker


def test_find_existing_issue_for_job_identity_returns_none_when_no_match():
    result = m4.find_existing_issue_for_job_identity(
        [{"url": "u1", "body": "no markers here"}],
        workflow_name="wf",
        job_name="job",
    )
    assert result is None


def test_parse_batch_agent_json_with_decisions_key():
    payload = json.dumps({
        "decisions": [
            {
                "workflow_name": "wf",
                "job_name": "job",
                "job_urls": ["u1"],
                "deterministic": True,
                "confidence": "high",
                "signature": "sig",
                "error_excerpt": "err",
                "reason": "r",
                "create_issue": True,
                "draft_slack": True,
                "issue_title": "title",
                "issue_body": "body",
                "slack_text": "text",
            }
        ]
    })
    result = m4.parse_batch_agent_json(payload)
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["workflow_name"] == "wf"


def test_extract_repo_paths_finds_known_prefixes():
    text = "Error in tt_metal/llrt/foo.cpp and tests/unit/test_bar.py"
    paths = m4.extract_repo_paths(text)
    assert any("tt_metal" in p for p in paths)
    assert any("tests" in p for p in paths)


def test_owners_for_paths_uses_last_matching_rule():
    rules = [
        ("*", ["default"]),
        ("tests/", ["test_owner"]),
    ]
    owners = m4.owners_for_paths(["tests/foo.py"], rules)
    assert "test_owner" in owners
