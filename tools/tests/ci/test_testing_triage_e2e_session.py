from __future__ import annotations

from tools.ci.testing_triage_e2e_session import build_bot_response, parse_issue_number


def test_parse_issue_number_from_text() -> None:
    text = "Issue: https://github.com/ebanerjeeTT/issue_dump/issues/1234"
    assert parse_issue_number(text) == 1234


def test_build_bot_response_fix_request_contains_mock_pr() -> None:
    text = build_bot_response(
        issue_number=77,
        progress={"defer_disable": False},
        fix_request={"requested": True},
        mock_github_owner="test-owner",
    )
    assert "Mock draft PR" in text
    assert "mock-77" in text
