"""Contract tests for Slice 6: M5 issue lifecycle management.

Validates thread follow-up logic, assignment cadence, and
state-driven lifecycle transitions.
"""

from __future__ import annotations

from tools.ci.m5_manage_issue_lifecycle import (
    candidate_github_owners_from_text,
    extract_repo_paths,
    issue_numbers_from_text,
    message_replies,
    parse_codeowners,
)


def test_extract_repo_paths_detects_known_prefixes():
    text = "See tt_metal/device/foo.h and ttnn/ops/bar.cpp"
    paths = extract_repo_paths(text)
    assert any("tt_metal" in p for p in paths)
    assert any("ttnn" in p for p in paths)


def test_candidate_github_owners_deduplicates():
    rules = [
        ("tt_metal/", ["owner_a", "owner_b"]),
        ("ttnn/", ["owner_a", "owner_c"]),
    ]
    owners = candidate_github_owners_from_text(
        "error in tt_metal/foo.cpp and ttnn/bar.cpp",
        rules,
    )
    assert len(set(owners)) == len(owners)
    assert "owner_a" in owners


def test_parse_codeowners_drops_teams(tmp_path):
    path = tmp_path / "CODEOWNERS"
    path.write_text("* @org/team @individual\n")
    rules = parse_codeowners(path)
    owners_flat = [o for _, ows in rules for o in ows]
    assert "individual" in owners_flat
    assert all("/" not in o for o in owners_flat)


def test_issue_numbers_from_text_extracts_issue_dump_urls():
    text = "https://github.com/ebanerjeeTT/issue_dump/issues/123 and #456"
    nums = issue_numbers_from_text(text)
    assert 123 in nums


def test_message_replies_handles_thread_replies_key():
    msg = {"thread_replies": [{"ts": "1.0"}, {"ts": "2.0"}]}
    assert len(message_replies(msg)) == 2


def test_message_replies_falls_back_to_replies_key():
    msg = {"replies": [{"ts": "3.0"}]}
    assert len(message_replies(msg)) == 1


def test_message_replies_returns_empty_for_missing():
    assert message_replies({}) == []
