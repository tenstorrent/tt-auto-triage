"""Tests for tools.ci.common.guarded and guarded_gh.py helpers."""

from __future__ import annotations

from unittest.mock import patch

from tools.ci.common.guarded import run_guarded_gh
from tools.ci.guarded_gh import has_option, validate


def test_run_guarded_gh_builds_command_string():
    with patch("tools.ci.common.guarded.run") as mock_run:
        mock_run.return_value.returncode = 0
        run_guarded_gh(["gh", "issue", "list", "--repo", "org/repo"])
        args, kwargs = mock_run.call_args
        cmd = args[0]
        assert cmd[-2] == "--command"
        assert "gh" in cmd[-1]
        assert "issue" in cmd[-1]


def test_run_guarded_gh_injects_token():
    with patch("tools.ci.common.guarded.run") as mock_run:
        mock_run.return_value.returncode = 0
        run_guarded_gh(["gh", "issue", "list"], github_token="tok_123")
        _, kwargs = mock_run.call_args
        assert kwargs["env"] == {"GITHUB_TOKEN": "tok_123"}


def test_run_guarded_gh_no_token_no_env():
    with patch("tools.ci.common.guarded.run") as mock_run:
        mock_run.return_value.returncode = 0
        run_guarded_gh(["gh", "issue", "list"])
        _, kwargs = mock_run.call_args
        assert kwargs["env"] is None


def test_run_guarded_gh_custom_cmd():
    with patch("tools.ci.common.guarded.run") as mock_run:
        mock_run.return_value.returncode = 0
        run_guarded_gh(["gh", "run", "list"], guarded_gh_cmd=["python3", "/custom/guard.py"])
        args, _ = mock_run.call_args
        cmd = args[0]
        assert cmd[0] == "python3"
        assert cmd[1] == "/custom/guard.py"


def test_has_option_detects_flag_without_value():
    """has_option must return True even when the flag has no following token."""
    tokens = ["gh", "pr", "comment", "--repo", "org/repo", "--body"]
    assert has_option(tokens, "--body") is True


def test_has_option_detects_equals_form():
    tokens = ["gh", "pr", "create", "--title=hello"]
    assert has_option(tokens, "--title") is True


def test_has_option_returns_false_when_absent():
    tokens = ["gh", "pr", "create", "--title=hello"]
    assert has_option(tokens, "--body") is False


def test_validate_pr_comment_allows_body_at_end():
    """Regression: --body at end of tokens (no value after) must still pass validation."""
    tokens = ["gh", "pr", "comment", "--repo", "tenstorrent/tt-metal", "123", "--body", "msg"]
    decision = validate(tokens)
    assert decision.allowed, decision.reason
