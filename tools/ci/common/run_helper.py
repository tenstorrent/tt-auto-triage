"""Shared subprocess runner with standard error formatting."""

from __future__ import annotations

import os
import shlex
import subprocess


def run(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run *cmd* as a subprocess.

    When *env* is given the entries are merged into the current environment.
    On non-zero exit (and *check* is true) raise ``RuntimeError`` with
    stdout/stderr.
    """
    proc_env = None
    if env:
        proc_env = os.environ.copy()
        proc_env.update(env)
    proc = subprocess.run(cmd, text=True, capture_output=capture, env=proc_env, check=False)
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {' '.join(shlex.quote(x) for x in cmd)}\n"
            f"stdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"
        )
    return proc
