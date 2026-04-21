"""Offline Slack ID -> (github_login, github_name) index.

Scans recent commits in the target repo for GitHub `noreply` author emails
(format: ``{id}+{login}@users.noreply.github.com``), then matches the author
display name back to Slack users by display name / real name. This keeps the
fast path capable of populating `github_assignees` without any per-issue API
calls, at the cost of one `git log` invocation at startup.

Coverage is best-effort: authors who only commit with private non-noreply
emails will not be resolved, and the fast path simply omits `github_assignees`
for them (same behavior as before this file existed).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from .github import log

_NORE_EMAIL = re.compile(r"^(?:\d+\+)?([a-zA-Z0-9\-]+)@users\.noreply\.github\.com$")
_GIT_LOG_LIMIT = 8000


def _normalize(name: str) -> str:
    return " ".join(name.lower().split())


def _collect_noreply_authors(repo_root: Path) -> dict[str, dict[str, str]]:
    """Return ``{normalized_author_name: {github_login, github_name}}`` from git log."""
    if not repo_root.exists():
        return {}
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "log", "--all", f"-n{_GIT_LOG_LIMIT}",
             "--format=%an%x09%ae", "--no-merges"],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"  Warning: git log for identity index failed: {exc}")
        return {}
    if proc.returncode != 0:
        log(f"  Warning: git log exited {proc.returncode}: {proc.stderr[:200]}")
        return {}
    out: dict[str, dict[str, str]] = {}
    for line in proc.stdout.splitlines():
        name, _, email = line.partition("\t")
        m = _NORE_EMAIL.match(email.strip())
        if not m:
            continue
        login = m.group(1)
        if login.lower() in {"github-actions", "dependabot", "dependabot[bot]"}:
            continue
        key = _normalize(name)
        if key and key not in out:
            out[key] = {"github_login": login, "github_name": name.strip()}
    return out


def _slack_name_keys(user: dict[str, Any]) -> list[str]:
    """Derive candidate normalized name keys for a Slack user."""
    keys: list[str] = []
    for field in ("real_name", "display_name"):
        value = user.get(field) or ""
        if value:
            keys.append(_normalize(value))
    profile_real = (user.get("profile") or {}).get("real_name", "") if isinstance(user.get("profile"), dict) else ""
    if profile_real:
        keys.append(_normalize(profile_real))
    return [k for k in keys if k]


def build_identity_index(repo_root: Path, slack_directory: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Return ``{slack_id: {github_login, github_name}}`` for users we could resolve."""
    authors = _collect_noreply_authors(repo_root)
    if not authors:
        return {}
    index: dict[str, dict[str, str]] = {}
    for user in slack_directory:
        sid = user.get("id") or ""
        if not sid or user.get("is_bot") or user.get("deleted"):
            continue
        for key in _slack_name_keys(user):
            hit = authors.get(key)
            if hit:
                index[sid] = hit
                break
    log(f"  Identity index: {len(index)} Slack users resolved to GitHub logins")
    return index
