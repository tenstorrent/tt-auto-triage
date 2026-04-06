"""Tests for tools.ci.common.guarded."""

from __future__ import annotations

from unittest.mock import patch

from tools.ci.common.guarded import run_guarded_gh


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
