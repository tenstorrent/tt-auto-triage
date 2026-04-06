from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def fake_slack_api(monkeypatch: pytest.MonkeyPatch):
    """Patch ``common.slack`` POST and GET helpers to return configurable responses."""
    mock_form = MagicMock(return_value={"ok": True, "ts": "1234567890.123456"})
    mock_get_simple = MagicMock(return_value={"ok": True, "messages": []})
    mock_get = MagicMock(return_value={"ok": True, "messages": []})

    monkeypatch.setattr("tools.ci.common.slack.slack_api_form", mock_form)
    monkeypatch.setattr("tools.ci.common.slack.slack_api_get_simple", mock_get_simple)
    monkeypatch.setattr("tools.ci.common.slack.slack_api_get", mock_get)

    return {"form": mock_form, "get_simple": mock_get_simple, "get": mock_get}


@pytest.fixture()
def fake_github_api(monkeypatch: pytest.MonkeyPatch):
    """Patch ``common.github`` helpers to return empty/configurable responses."""
    mock_get = MagicMock(return_value={})
    mock_user = MagicMock(return_value={"login": "testuser", "name": "Test User", "email": ""})

    monkeypatch.setattr("tools.ci.common.github.github_api_get", mock_get)
    monkeypatch.setattr("tools.ci.common.github.github_user_info", mock_user)

    return {"get": mock_get, "user_info": mock_user}


@pytest.fixture()
def fake_guarded_gh(monkeypatch: pytest.MonkeyPatch):
    """Patch ``common.guarded.run_guarded_gh`` to return a successful no-op."""
    import subprocess

    proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    mock = MagicMock(return_value=proc)
    monkeypatch.setattr("tools.ci.common.guarded.run_guarded_gh", mock)
    return mock


@pytest.fixture()
def fake_state_dir(tmp_path: Path):
    """Create a ``tmp_path`` containing a valid minimal ``ci_triage_state.json``."""
    state_path = tmp_path / "ci_triage_state.json"
    state_path.write_text(
        json.dumps({"version": 1, "updated_at_utc": "2024-01-01T00:00:00Z", "items": []}),
        encoding="utf-8",
    )
    return {"dir": tmp_path, "state_path": state_path}
