"""Resolve likely owners for failing jobs from pipeline_reorg, owners.json, and CODEOWNERS."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .helpers import api_get, log

IGNORE_CODEOWNERS = {"@tenstorrent/codeowner-bypass", "@tenstorrent/metalium-developers-infra"}


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _github_user_info(gh_username: str, token: str | None = None) -> dict[str, str]:
    """Fetch a GitHub user's real name and email via the REST API."""
    try:
        data = api_get(f"https://api.github.com/users/{gh_username}", token)
        return {
            "name": data.get("name") or "",
            "email": data.get("email") or "",
        }
    except Exception as exc:
        log(f"  Warning: GitHub user lookup failed for {gh_username}: {exc}")
        return {"name": "", "email": ""}


def lookup_slack_id(
    query: str,
    slack_directory: list[dict[str, Any]],
) -> dict[str, str] | None:
    """Look up a real name or email in the Slack directory.

    Returns {"id": slack_id, "name": real_name} or None if no match.
    """
    query_norm = _normalize(query)
    if not query_norm:
        return None

    best_score = 0
    best_user: dict[str, Any] | None = None

    for user in slack_directory:
        if user.get("deleted") or user.get("is_bot"):
            continue
        for field in ("real_name", "display_name", "email"):
            val = user.get(field, "")
            if not val:
                continue
            val_norm = _normalize(val)
            if not val_norm:
                continue
            if val_norm == query_norm:
                return {"id": user["id"], "name": user.get("real_name") or user.get("display_name") or query}
            if query_norm in val_norm and len(query_norm) >= 3:
                score = len(query_norm) / len(val_norm)
                if score > best_score:
                    best_score = score
                    best_user = user

    if best_user and best_score >= 0.5:
        return {"id": best_user["id"], "name": best_user.get("real_name") or best_user.get("display_name") or query}
    return None


def load_owners_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        log(f"  Warning: {path} not found")
        return []
    data = json.loads(path.read_text())
    return data.get("contains", [])


def load_pipeline_reorg_owners(reorg_dir: Path) -> list[dict[str, Any]]:
    """Parse pipeline_reorg YAMLs for name + owner_id entries.

    Uses simple regex parsing to avoid a PyYAML dependency.
    """
    entries: list[dict[str, Any]] = []
    if not reorg_dir.exists():
        log(f"  Warning: {reorg_dir} not found")
        return entries
    for yaml_file in sorted(reorg_dir.glob("*.yaml")):
        text = yaml_file.read_text()
        current_name: str | None = None
        for line in text.splitlines():
            name_match = re.match(r'^- name:\s*"(.+)"', line)
            if name_match:
                current_name = name_match.group(1)
                continue
            owner_match = re.match(r'^\s+owner_id:\s*(.+)', line)
            if owner_match and current_name:
                rest = owner_match.group(1).strip()
                raw_id = rest.split("#")[0].strip().split()[0]
                name = rest.split("#", 1)[1].strip() if "#" in rest else ""
                entries.append({"name": current_name, "id": raw_id, "owner_name": name})
                current_name = None
    return entries


def load_codeowners(path: Path) -> dict[str, list[str]]:
    """Parse CODEOWNERS file into {pattern: [github_username, ...]} mappings.

    Filters out team handles and generic bypass accounts.
    """
    rules: dict[str, list[str]] = {}
    if not path.exists():
        log(f"  Warning: {path} not found")
        return rules
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        pattern = parts[0]
        owners = [
            o.lstrip("@") for o in parts[1:]
            if o.startswith("@") and o not in IGNORE_CODEOWNERS and "/" not in o
        ]
        if owners:
            rules[pattern] = owners
    return rules


def _gh_users_to_slack(
    gh_usernames: list[str],
    slack_directory: list[dict[str, Any]],
    source: str,
    github_token: str | None = None,
) -> list[dict[str, str]]:
    """Resolve GitHub usernames to Slack IDs.

    Flow: GitHub username -> GitHub API (real name + email) -> Slack directory lookup.
    """
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for gh_user in gh_usernames:
        if gh_user in seen:
            continue
        seen.add(gh_user)

        info = _github_user_info(gh_user, github_token)
        real_name = info["name"]
        email = info["email"]

        resolved = None
        if real_name:
            resolved = lookup_slack_id(real_name, slack_directory)
        if not resolved and email:
            resolved = lookup_slack_id(email, slack_directory)

        if resolved:
            result.append({"id": resolved["id"], "name": resolved["name"]})
        else:
            display = real_name or gh_user
            result.append({"id": f"@{gh_user}", "name": display, "source": source})
    return result


def _match_codeowners(
    workflow_name: str,
    codeowners: dict[str, list[str]],
    slack_directory: list[dict[str, Any]],
    github_token: str | None = None,
) -> list[dict[str, str]]:
    """Try to match workflow name to a CODEOWNERS pattern.

    Looks for .github/workflows/<workflow>.yaml patterns and returns
    matching individual GitHub usernames resolved to Slack IDs.
    """
    wf_lower = workflow_name.lower().replace(" ", "-").replace("(", "").replace(")", "")
    candidates: list[str] = []
    for pattern, owners in codeowners.items():
        pat_lower = pattern.lower()
        if ".github/workflows/" in pat_lower:
            pat_base = pat_lower.rsplit("/", 1)[-1].replace(".yaml", "").replace(".yml", "").replace("*", "")
            if pat_base and pat_base in wf_lower:
                candidates.extend(owners)
    if not candidates:
        return []
    return _gh_users_to_slack(candidates, slack_directory, "CODEOWNERS", github_token)


def resolve_owners(
    workflow_name: str,
    job_name: str,
    owners_json: list[dict[str, Any]],
    pipeline_owners: list[dict[str, Any]],
    codeowners: dict[str, list[str]] | None = None,
    agent_suggested: list[str] | None = None,
    slack_directory: list[dict[str, Any]] | None = None,
    github_token: str | None = None,
) -> list[dict[str, str]]:
    """Resolve owners from multiple sources with priority: pipeline_reorg > owners.json > CODEOWNERS > agent."""
    combined = f"{workflow_name} / {job_name}".lower()
    job_lower = job_name.lower()
    slack_dir = slack_directory or []

    for entry in pipeline_owners:
        entry_name = entry["name"].lower()
        if entry_name in job_lower or job_lower in entry_name:
            return [{"id": entry["id"], "name": entry.get("owner_name", "")}]

    for rec in owners_json:
        component = str(rec.get("job-name-component", "")).lower()
        if not component:
            continue
        if component in combined or combined in component:
            owner = rec.get("owner")
            if isinstance(owner, list):
                return [{"id": o["id"], "name": o.get("name", "")} for o in owner]
            if isinstance(owner, dict):
                return [{"id": owner["id"], "name": owner.get("name", "")}]

    if codeowners:
        co_owners = _match_codeowners(workflow_name, codeowners, slack_dir, github_token)
        if co_owners:
            return co_owners

    if agent_suggested:
        return _gh_users_to_slack(agent_suggested, slack_dir, "agent", github_token)

    return []
