"""Wrapper around guarded_gh.py for programmatic use."""

from __future__ import annotations

import shlex
import subprocess
import sys

from .run_helper import run

_GUARDED_GH_DEFAULT: list[str] = [sys.executable, "tools/ci/guarded_gh.py"]


def run_guarded_gh(
    tokens: list[str],
    *,
    github_token: str | None = None,
    check: bool = True,
    capture: bool = True,
    guarded_gh_cmd: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Build a ``--command`` string and run it through ``guarded_gh.py``.

    When *github_token* is supplied it is injected via the ``GITHUB_TOKEN``
    environment variable (the pattern used by M4/M5/M6/issue_lifecycle).
    When omitted the process inherits ambient ``gh`` auth (load_previous /
    execute_disable pattern).
    """
    command = " ".join(shlex.quote(tok) for tok in tokens)
    cmd = list(guarded_gh_cmd or _GUARDED_GH_DEFAULT) + ["--command", command]
    env = {"GITHUB_TOKEN": github_token} if github_token else None
    return run(cmd, env=env, check=check, capture=capture)
