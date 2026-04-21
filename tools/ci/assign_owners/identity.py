"""Offline Slack ID -> (github_login, github_name) index.

Scans recent commits in the target repo and exploits two facts:

1. A commit authored with a ``noreply.github.com`` email embeds the author's
   GitHub login right in the email local part
   (``{id}+{login}@users.noreply.github.com``).
2. Many contributors use multiple ``(name, email)`` combinations for the same
   email (e.g. real name + GitHub login as alternate ``git config user.name``).
   So a single email shared across commits reveals both the real name and the
   login even when the email itself is private (e.g. ``@tenstorrent.com``).

Given those two facts, we build:
  * ``email -> {names}`` — every author name that ever committed from an email
  * ``normalized_name -> {emails}`` — every email a given display name used

Then for each Slack user we:
  1. look up their normalized real/display name in the name map,
  2. collect every email they've used,
  3. among every name ever paired with those emails, pick one that looks like
     a GitHub login (matches ``^[a-z0-9][a-z0-9-]*[a-z0-9]$``) and isn't the
     Slack user's real name.

Coverage is best-effort. Authors who only commit from private emails and never
from a "login-shaped" alternate name stay unresolved; the fast path falls back
to ``github_assignees=[]`` for them, which is the behavior before this file
existed.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from .github import log

_NORE_EMAIL = re.compile(r"^(?:\d+\+)?([a-zA-Z0-9\-]+)@users\.noreply\.github\.com$")
_LOGIN_SHAPED = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-]{0,38}$")
_GIT_LOG_LIMIT = 50000
_BOT_LOGINS = {"github-actions", "dependabot", "dependabot[bot]", "copilot[bot]"}


def _normalize(name: str) -> str:
    return " ".join(name.lower().split())


def _collect_git_authors(repo_root: Path) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, str]]:
    """Return (email->names, normalized_name->emails, login->display_name_seen)."""
    email_to_names: dict[str, set[str]] = {}
    name_to_emails: dict[str, set[str]] = {}
    login_display: dict[str, str] = {}
    if not repo_root.exists():
        return email_to_names, name_to_emails, login_display
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "log", "--all", f"-n{_GIT_LOG_LIMIT}",
             "--format=%an%x09%ae", "--no-merges"],
            capture_output=True, text=True, timeout=120, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"  Warning: git log for identity index failed: {exc}")
        return email_to_names, name_to_emails, login_display
    if proc.returncode != 0:
        log(f"  Warning: git log exited {proc.returncode}: {proc.stderr[:200]}")
        return email_to_names, name_to_emails, login_display
    for line in proc.stdout.splitlines():
        name, _, email = line.partition("\t")
        name = name.strip()
        email = email.strip()
        if not name or not email:
            continue
        email_key = email.lower()
        email_to_names.setdefault(email_key, set()).add(name)
        name_to_emails.setdefault(_normalize(name), set()).add(email)
        m = _NORE_EMAIL.match(email)
        if m:
            login = m.group(1)
            if login.lower() not in _BOT_LOGINS:
                login_display.setdefault(login, name)
    return email_to_names, name_to_emails, login_display


def _slack_name_keys(user: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for field in ("real_name", "display_name"):
        value = user.get(field) or ""
        if value:
            keys.append(_normalize(value))
    profile = user.get("profile") if isinstance(user.get("profile"), dict) else None
    if profile:
        profile_real = profile.get("real_name") or ""
        if profile_real:
            keys.append(_normalize(profile_real))
    return [k for k in keys if k]


def _best_login_for_slack_user(
    user: dict[str, Any],
    email_to_names: dict[str, set[str]],
    name_to_emails: dict[str, set[str]],
) -> tuple[str, str]:
    """Return (github_login, github_display_name) or ("", "")."""
    real_name = (user.get("real_name") or user.get("display_name") or "").strip()
    real_norm = _normalize(real_name)
    if not real_norm:
        return "", ""
    emails = set()
    for key in _slack_name_keys(user):
        emails |= name_to_emails.get(key, set())
    if not emails:
        return "", ""
    # First pass: prefer noreply emails (login is authoritative).
    for email in emails:
        m = _NORE_EMAIL.match(email)
        if m:
            login = m.group(1)
            if login.lower() not in _BOT_LOGINS:
                return login, real_name
    # Second pass: any alternate author name sharing these emails that looks like a login.
    candidates: set[str] = set()
    for email in emails:
        candidates |= email_to_names.get(email.lower(), set())
    for candidate in sorted(candidates, key=lambda n: (len(n), n)):
        if _normalize(candidate) == real_norm:
            continue
        if candidate.lower() in _BOT_LOGINS:
            continue
        if " " in candidate:
            continue
        if _LOGIN_SHAPED.match(candidate):
            return candidate, real_name
    return "", ""


def build_identity_index(repo_root: Path, slack_directory: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Return ``{slack_id: {github_login, github_name}}`` for Slack users we could resolve."""
    email_to_names, name_to_emails, _ = _collect_git_authors(repo_root)
    if not name_to_emails:
        return {}
    index: dict[str, dict[str, str]] = {}
    for user in slack_directory:
        sid = user.get("id") or ""
        if not sid or user.get("is_bot") or user.get("deleted"):
            continue
        login, display = _best_login_for_slack_user(user, email_to_names, name_to_emails)
        if login:
            index[sid] = {"github_login": login, "github_name": display}
    log(f"  Identity index: {len(index)} Slack users resolved to GitHub logins")
    return index
