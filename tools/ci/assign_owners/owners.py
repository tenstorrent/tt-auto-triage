from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any

from .github import api_get, log

IGNORE_CODEOWNERS = {
    "@tenstorrent/codeowner-bypass",
    "@tenstorrent/metalium-developers-infra",
}


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _github_user_info(gh_username: str, token: str | None = None) -> dict[str, str]:
    try:
        data = api_get(f"https://api.github.com/users/{gh_username}", token)
        return {
            "name": data.get("name") or "",
            "email": data.get("email") or "",
        }
    except Exception as exc:
        log(f"  Warning: GitHub user lookup failed for {gh_username}: {exc}")
        return {"name": "", "email": ""}


def _slack_user_for(query: str, slack_directory: list[dict[str, Any]]) -> dict[str, str]:
    """Find the best Slack match for `query`; returns id/real_name/display_name or empty strings."""
    query_norm = _normalize(query)
    if not query_norm:
        return {"id": "", "real_name": "", "display_name": ""}
    best_score = 0.0
    best: dict[str, str] = {"id": "", "real_name": "", "display_name": ""}
    for user in slack_directory:
        if user.get("deleted") or user.get("is_bot"):
            continue
        for field in ("real_name", "display_name", "email", "username"):
            value_norm = _normalize(user.get(field, ""))
            if not value_norm:
                continue
            if value_norm == query_norm:
                return {
                    "id": user.get("id", ""),
                    "real_name": user.get("real_name", ""),
                    "display_name": user.get("display_name", ""),
                }
            if query_norm in value_norm and len(query_norm) >= 3:
                score = len(query_norm) / len(value_norm)
                if score > best_score:
                    best_score = score
                    best = {
                        "id": user.get("id", ""),
                        "real_name": user.get("real_name", ""),
                        "display_name": user.get("display_name", ""),
                    }
    return best if best_score >= 0.5 else {"id": "", "real_name": "", "display_name": ""}


def lookup_slack_id(query: str, slack_directory: list[dict[str, Any]]) -> str:
    return _slack_user_for(query, slack_directory)["id"]


def _slack_user_by_id(slack_id: str, slack_directory: list[dict[str, Any]]) -> dict[str, Any]:
    if not slack_id:
        return {}
    for user in slack_directory:
        if user.get("id") == slack_id:
            return user
    return {}


def _slack_name_for_id(slack_id: str, slack_directory: list[dict[str, Any]]) -> str:
    user = _slack_user_by_id(slack_id, slack_directory)
    return user.get("real_name") or user.get("display_name") or ""


def build_commit_identity_index(
    repo_root: Path | str,
    max_commits: int = 5000,
) -> dict[str, list[dict[str, str]]]:
    """Scan the target repo's git log once and build a name/email -> github-login index.

    Uses commits authored by Tenstorrent devs as the authoritative source: the
    `users.noreply.github.com` email format directly encodes the GitHub login,
    paired with the author's real name in the commit metadata. This gives us a
    surefire reverse lookup for anyone who has ever contributed to the target repo.

    Returns a dict with two keys:
      - "by_name": {normalized_real_name: [{"login": ..., "name": ..., "email": ...}]}
      - "by_email": {normalized_email: [{"login": ..., "name": ..., "email": ...}]}
    """
    index: dict[str, list[dict[str, str]]] = {"by_name": {}, "by_email": {}}
    repo_root_path = Path(repo_root)
    if not repo_root_path.exists():
        return index
    cmd = [
        "git", "-C", str(repo_root_path), "log",
        f"-n{max_commits}",
        "--pretty=%an%x09%ae",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        log(f"  Warning: failed to build commit identity index: {exc}")
        return index

    seen_pairs: set[tuple[str, str, str]] = set()
    for line in proc.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        name, email = parts[0].strip(), parts[1].strip()
        login = _extract_github_login(email)
        if not login:
            continue
        key = (name.lower(), email.lower(), login.lower())
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        record = {"login": login, "name": name, "email": email}
        name_norm = _normalize(name)
        if name_norm:
            index["by_name"].setdefault(name_norm, []).append(record)
        email_norm = email.lower()
        if email_norm:
            index["by_email"].setdefault(email_norm, []).append(record)
    return index


def _github_from_commit_index(
    *,
    email: str,
    real_name: str,
    commit_identity_index: dict[str, list[dict[str, str]]],
) -> dict[str, str]:
    """Return {login, name} for an exact/normalized match in the commit identity index."""
    if not commit_identity_index:
        return {"login": "", "name": ""}
    if email:
        hits = commit_identity_index.get("by_email", {}).get(email.lower(), [])
        logins = {h["login"] for h in hits}
        if len(logins) == 1:
            rec = hits[0]
            return {"login": rec["login"], "name": rec["name"] or real_name}
    if real_name:
        hits = commit_identity_index.get("by_name", {}).get(_normalize(real_name), [])
        logins = {h["login"] for h in hits}
        if len(logins) == 1:
            rec = hits[0]
            return {"login": rec["login"], "name": rec["name"] or real_name}
    return {"login": "", "name": ""}


def _github_from_identity(
    *,
    email: str = "",
    real_name: str = "",
    github_token: str | None = None,
    commit_identity_index: dict[str, list[dict[str, str]]] | None = None,
) -> dict[str, str]:
    """Reverse-lookup: (email, real_name) -> (github login, display name).

    Tries sources in priority order:
      1. Local commit identity index (offline, authoritative when available).
      2. GitHub user search by public email.
      3. GitHub user search by quoted full name + in:fullname.

    Returns empty strings when no confident match.
    """

    if commit_identity_index:
        hit = _github_from_commit_index(
            email=email,
            real_name=real_name,
            commit_identity_index=commit_identity_index,
        )
        if hit["login"]:
            return hit

    if not github_token:
        return {"login": "", "name": ""}

    def _accept(login: str) -> dict[str, str]:
        if not login:
            return {"login": "", "name": ""}
        info = _github_user_info(login, github_token)
        return {"login": login, "name": info.get("name") or real_name}

    if email and "@" in email:
        try:
            data = api_get(
                "https://api.github.com/search/users?q="
                + urllib.parse.quote(f"{email} in:email"),
                github_token,
            )
            items = data.get("items", [])
            if len(items) == 1:
                return _accept(items[0].get("login", ""))
        except Exception as exc:  # noqa: BLE001
            log(f"  Warning: GitHub email search failed for {email}: {exc}")

    if real_name:
        try:
            data = api_get(
                "https://api.github.com/search/users?q="
                + urllib.parse.quote(f'"{real_name}" in:fullname type:user'),
                github_token,
            )
            items = data.get("items", [])
            if len(items) == 1:
                return _accept(items[0].get("login", ""))
        except Exception as exc:  # noqa: BLE001
            log(f"  Warning: GitHub name search failed for '{real_name}': {exc}")

    return {"login": "", "name": ""}


def _enrich_slack_only_with_github(
    slack_ids: list[str],
    slack_names_seed: list[str],
    slack_directory: list[dict[str, Any]],
    github_token: str | None,
    commit_identity_index: dict[str, list[dict[str, str]]] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """For a slack-only tier (pipeline_reorg / owners_json), resolve GitHub
    login+name per Slack ID and return (slack_names, github_logins, github_names).
    slack_names_seed provides authoritative names when available (e.g. pipeline_reorg comment).
    """
    slack_names: list[str] = []
    github_logins: list[str] = []
    github_names: list[str] = []
    for idx, sid in enumerate(slack_ids):
        su = _slack_user_by_id(sid, slack_directory)
        seeded = slack_names_seed[idx] if idx < len(slack_names_seed) else ""
        real_name = seeded or su.get("real_name") or su.get("display_name") or ""
        slack_names.append(real_name)
        gh_hit = _github_from_identity(
            email=su.get("email", ""),
            real_name=real_name,
            github_token=github_token,
            commit_identity_index=commit_identity_index,
        )
        if gh_hit["login"]:
            github_logins.append(gh_hit["login"])
            github_names.append(gh_hit["name"])
    return slack_names, github_logins, github_names


def load_owners_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        log(f"  Warning: {path} not found")
        return []
    data = json.loads(path.read_text())
    return data.get("contains", [])


def load_pipeline_reorg_owners(reorg_dir: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if not reorg_dir.exists():
        log(f"  Warning: {reorg_dir} not found")
        return entries
    for yaml_file in sorted(reorg_dir.glob("*.yaml")):
        text = yaml_file.read_text()
        current_name: str | None = None
        for line in text.splitlines():
            name_match = re.match(r"^- name:\s*[\"']?(.+?)[\"']?\s*$", line)
            if name_match:
                current_name = name_match.group(1)
                continue
            owner_match = re.match(r'^\s+owner_id:\s*(.+)', line)
            if owner_match and current_name:
                remainder = owner_match.group(1).strip()
                raw_id = remainder.split("#")[0].strip().split()[0]
                owner_name = remainder.split("#", 1)[1].strip() if "#" in remainder else ""
                entries.append({"name": current_name, "id": raw_id, "owner_name": owner_name})
                current_name = None
    return entries


def load_codeowners(path: Path) -> dict[str, list[str]]:
    rules: dict[str, list[str]] = {}
    if not path.exists():
        log(f"  Warning: {path} not found")
        return rules
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        pattern = parts[0]
        owners = [
            owner.lstrip("@")
            for owner in parts[1:]
            if owner.startswith("@") and owner not in IGNORE_CODEOWNERS and "/" not in owner
        ]
        if owners:
            rules[pattern] = owners
    return rules


def _workflow_file_candidates(workflow_name: str, repo_root: Path) -> list[Path]:
    workflows_dir = repo_root / ".github" / "workflows"
    if not workflows_dir.exists():
        return []

    workflow_norm = _normalize(workflow_name)
    candidates: list[Path] = []
    for workflow_file in sorted(workflows_dir.glob("*.y*ml")):
        stem_norm = _normalize(workflow_file.stem)
        if not stem_norm:
            continue
        if stem_norm in workflow_norm or workflow_norm in stem_norm:
            candidates.append(workflow_file)
    return candidates


def _codeowners_match(rel_path: str, pattern: str) -> bool:
    """Minimal CODEOWNERS matcher against a relative file path."""
    p = pattern.lstrip("/")
    if p.endswith("/"):
        return rel_path.startswith(p)
    if "*" in p or "?" in p:
        return fnmatch.fnmatch(rel_path, p)
    return rel_path == p or rel_path.endswith("/" + p)


def _codeowners_matches(
    workflow_name: str,
    codeowners: dict[str, list[str]],
    repo_root: Path,
) -> list[str]:
    """Resolve CODEOWNERS against the actual workflow file(s) for `workflow_name`."""
    workflow_files = _workflow_file_candidates(workflow_name, repo_root)
    if not workflow_files:
        return []
    rel_paths = [str(path.relative_to(repo_root)) for path in workflow_files]
    matches: list[str] = []
    for pattern, owners in codeowners.items():
        for rel_path in rel_paths:
            if _codeowners_match(rel_path, pattern):
                matches.extend(owners)
                break
    return list(dict.fromkeys(matches))


def _extract_github_login(email: str) -> str:
    match = re.match(r"^(?:\d+\+)?([^@]+)@users\.noreply\.github\.com$", email.strip(), re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).strip()


def _git_history_candidates(
    workflow_name: str,
    repo_root: Path,
    git_history_max_commits: int,
) -> tuple[list[str], list[str]]:
    """Return (logins, raw_emails) from recent commits touching the matching workflow file(s)."""
    workflow_files = _workflow_file_candidates(workflow_name, repo_root)
    if not workflow_files:
        return [], []

    rel_paths = [str(path.relative_to(repo_root)) for path in workflow_files]
    cmd = [
        "git", "-C", str(repo_root), "log",
        f"-n{git_history_max_commits}",
        "--pretty=%ae%x09%ce",
        "--", *rel_paths,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        log(f"  Warning: git history owner lookup failed for '{workflow_name}': {exc}")
        return [], []

    logins: list[str] = []
    emails: list[str] = []
    seen_logins: set[str] = set()
    seen_emails: set[str] = set()
    for line in proc.stdout.splitlines():
        for email in line.split("\t"):
            email = email.strip()
            if not email:
                continue
            login = _extract_github_login(email)
            if login:
                if login not in seen_logins:
                    seen_logins.add(login)
                    logins.append(login)
            else:
                if email not in seen_emails:
                    seen_emails.add(email)
                    emails.append(email)
    return logins, emails


def _resolve_github_users(
    github_users: list[str],
    slack_directory: list[dict[str, Any]],
    github_token: str | None,
    extra_email_queries: list[str] | None = None,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Resolve github logins into (logins, gh_names, slack_ids, slack_names) aligned by input order.

    extra_email_queries (optional) are raw emails that should also be tried against Slack
    when no github login produces a match — useful for real-email git-history authors.
    """
    gh_names: list[str] = []
    slack_ids: list[str] = []
    slack_names: list[str] = []
    seen_slack: set[str] = set()
    for github_user in github_users:
        info = _github_user_info(github_user, github_token)
        slack_hit: dict[str, str] = {"id": "", "real_name": "", "display_name": ""}
        for query in (info["name"], info["email"], github_user):
            if not query:
                continue
            slack_hit = _slack_user_for(query, slack_directory)
            if slack_hit["id"]:
                break
        gh_names.append(info["name"])
        sid = slack_hit["id"]
        if sid and sid not in seen_slack:
            seen_slack.add(sid)
            slack_ids.append(sid)
            slack_names.append(slack_hit["real_name"] or slack_hit["display_name"])

    for email in extra_email_queries or []:
        slack_hit = _slack_user_for(email, slack_directory)
        sid = slack_hit["id"]
        if sid and sid not in seen_slack:
            seen_slack.add(sid)
            slack_ids.append(sid)
            slack_names.append(slack_hit["real_name"] or slack_hit["display_name"])

    return list(dict.fromkeys(github_users)), gh_names, slack_ids, slack_names


def _empty() -> dict[str, object]:
    return {
        "source": "none",
        "github_assignees": [],
        "github_names": [],
        "slack_assignees": [],
        "slack_names": [],
    }


def resolve_owners(
    workflow_name: str,
    job_name: str,
    owners_json: list[dict[str, Any]],
    pipeline_owners: list[dict[str, Any]],
    codeowners: dict[str, list[str]],
    slack_directory: list[dict[str, Any]],
    github_token: str | None,
    repo_root: Path | str = Path("tt-metal"),
    git_history_max_commits: int = 100,
    commit_identity_index: dict[str, list[dict[str, str]]] | None = None,
) -> dict[str, object]:
    """Resolve owners with 4-tier priority: pipeline_reorg > owners.json > CODEOWNERS > git history.

    Returns a dict with:
      - source: which tier matched ("none" if no match)
      - github_assignees: list of GitHub logins (may be empty for slack-only sources)
      - github_names: real names parallel to github_assignees
      - slack_assignees: list of Slack user ids
      - slack_names: real names parallel to slack_assignees
    """
    combined = f"{workflow_name} / {job_name}".lower()
    job_lower = job_name.lower()
    repo_root_path = Path(repo_root)

    for entry in pipeline_owners:
        entry_name = entry["name"].lower()
        if entry_name in job_lower or job_lower in entry_name:
            slack_id = entry["id"]
            if not slack_id:
                continue
            slack_name_seed = entry.get("owner_name") or ""
            slack_names, gh_logins, gh_names = _enrich_slack_only_with_github(
                [slack_id], [slack_name_seed], slack_directory, github_token,
                commit_identity_index=commit_identity_index,
            )
            return {
                "source": "pipeline_reorg",
                "github_assignees": gh_logins,
                "github_names": gh_names,
                "slack_assignees": [slack_id],
                "slack_names": slack_names,
            }

    for record in owners_json:
        component = str(record.get("job-name-component", "")).lower()
        if not component or (component not in combined and combined not in component):
            continue
        owner = record.get("owner")
        if isinstance(owner, list):
            slack_ids = [e["id"] for e in owner if e.get("id")]
            seeded_names = [e.get("name") or "" for e in owner if e.get("id")]
        elif isinstance(owner, dict):
            slack_ids = [owner["id"]] if owner.get("id") else []
            seeded_names = [owner.get("name") or ""] if owner.get("id") else []
        else:
            slack_ids = []
            seeded_names = []
        deduped: list[str] = []
        deduped_names: list[str] = []
        for sid, sn in zip(slack_ids, seeded_names):
            if sid not in deduped:
                deduped.append(sid)
                deduped_names.append(sn)
        slack_names, gh_logins, gh_names = _enrich_slack_only_with_github(
            deduped, deduped_names, slack_directory, github_token,
            commit_identity_index=commit_identity_index,
        )
        return {
            "source": "owners_json",
            "github_assignees": gh_logins,
            "github_names": gh_names,
            "slack_assignees": deduped,
            "slack_names": slack_names,
        }

    codeowners_users = _codeowners_matches(workflow_name, codeowners, repo_root_path)
    if codeowners_users:
        logins, gh_names, slack_ids, slack_names = _resolve_github_users(
            codeowners_users, slack_directory, github_token
        )
        return {
            "source": "CODEOWNERS",
            "github_assignees": logins,
            "github_names": gh_names,
            "slack_assignees": slack_ids,
            "slack_names": slack_names,
        }

    logins_hist, emails_hist = _git_history_candidates(
        workflow_name, repo_root_path, git_history_max_commits
    )
    if logins_hist or emails_hist:
        logins, gh_names, slack_ids, slack_names = _resolve_github_users(
            logins_hist, slack_directory, github_token, extra_email_queries=emails_hist
        )
        if logins or slack_ids:
            return {
                "source": "git_history",
                "github_assignees": logins,
                "github_names": gh_names,
                "slack_assignees": slack_ids,
                "slack_names": slack_names,
            }

    return _empty()
