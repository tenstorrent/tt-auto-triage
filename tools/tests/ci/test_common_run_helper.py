"""Tests for tools.ci.common.run_helper."""

from __future__ import annotations

import pytest

from tools.ci.common.run_helper import run


def test_run_success():
    proc = run(["echo", "hello"])
    assert proc.returncode == 0
    assert "hello" in proc.stdout


def test_run_failure_raises():
    with pytest.raises(RuntimeError, match="Command failed"):
        run(["false"])


def test_run_no_check():
    proc = run(["false"], check=False)
    assert proc.returncode != 0


def test_run_env_injection():
    proc = run(["env"], env={"TEST_MARKER_XYZ": "42"})
    assert "TEST_MARKER_XYZ=42" in proc.stdout
