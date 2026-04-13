from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .github import api_get, log

IGNORE_CODEOWNERS = {
    "@tenstorrent/codeowner-bypass",
    "@tenstorrent/metalium-developers-infra",
}


def _is_slack_user_id(slack_id: str) -> bool:
    """Slack user IDs start with U or W; usergroup IDs start with S."""
    return bool(slack_id) and slack_id[0] in ("U", "W")


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


def lookup_slack_id(query: str, slack_directory: list[dict[str, Any]]) -> str:
    query_norm = _normalize(query)
    if not query_norm:
        return ""
    best_score = 0.0
    best_id = ""
    for user in slack_directory:
        if user.get("deleted") or user.get("is_bot"):
            continue
        for field in ("real_name", "display_name", "email", "username"):
            value = user.get(field, "")
            value_norm = _normalize(value)
            if not value_norm:
                continue
            if value_norm == query_norm:
                return user.get("id", "")
            if query_norm in value_norm and len(query_norm) >= 3:
                score = len(query_norm) / len(value_norm)
                if score > best_score:
                    best_score = score
                    best_id = user.get("id", "")
    return best_id if best_score >= 0.5 else ""


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
            name_match = re.match(r'^- name:\s*"(.+)"', line)
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


def _codeowners_matches(workflow_name: str, codeowners: dict[str, list[str]]) -> list[str]:
    workflow_lower = workflow_name.lower().replace(" ", "-").replace("(", "").replace(")", "")
    matches: list[str] = []
    for pattern, owners in codeowners.items():
        pattern_lower = pattern.lower()
        if ".github/workflows/" not in pattern_lower:
            continue
        pattern_base = (
            pattern_lower.rsplit("/", 1)[-1]
            .replace(".yaml", "")
            .replace(".yml", "")
            .replace("*", "")
        )
        if pattern_base and pattern_base in workflow_lower:
            matches.extend(owners)
    return list(dict.fromkeys(matches))


def _resolve_github_users(
    github_users: list[str],
    slack_directory: list[dict[str, Any]],
    github_token: str | None,
) -> tuple[list[str], list[str]]:
    slack_ids: list[str] = []
    seen_slack: set[str] = set()
    for github_user in github_users:
        user_info = _github_user_info(github_user, github_token)
        slack_id = ""
        if user_info["name"]:
            slack_id = lookup_slack_id(user_info["name"], slack_directory)
        if not slack_id and user_info["email"]:
            slack_id = lookup_slack_id(user_info["email"], slack_directory)
        if not slack_id:
            slack_id = lookup_slack_id(github_user, slack_directory)
        if slack_id and slack_id not in seen_slack:
            seen_slack.add(slack_id)
            slack_ids.append(slack_id)
    return list(dict.fromkeys(github_users)), slack_ids


def resolve_owners(
    workflow_name: str,
    job_name: str,
    owners_json: list[dict[str, Any]],
    pipeline_owners: list[dict[str, Any]],
    codeowners: dict[str, list[str]],
    slack_directory: list[dict[str, Any]],
    github_token: str | None,
) -> dict[str, object]:
    combined = f"{workflow_name} / {job_name}".lower()
    job_lower = job_name.lower()

    for entry in pipeline_owners:
        entry_name = entry["name"].lower()
        if entry_name in job_lower or job_lower in entry_name:
            slack_id = entry["id"]
            individual_ids = [slack_id] if slack_id and _is_slack_user_id(slack_id) else []
            if individual_ids:
                return {
                    "source": "pipeline_reorg",
                    "github_assignees": [],
                    "slack_assignees": individual_ids,
                }
            log(f"  Skipping group Slack ID from pipeline_reorg for {entry_name}")
            break

    for record in owners_json:
        component = str(record.get("job-name-component", "")).lower()
        if not component or (component not in combined and combined not in component):
            continue
        owner = record.get("owner")
        if isinstance(owner, list):
            slack_ids = [entry["id"] for entry in owner if entry.get("id")]
        elif isinstance(owner, dict):
            slack_ids = [owner["id"]] if owner.get("id") else []
        else:
            slack_ids = []
        individual_ids = [sid for sid in dict.fromkeys(slack_ids) if _is_slack_user_id(sid)]
        if individual_ids:
            return {
                "source": "owners_json",
                "github_assignees": [],
                "slack_assignees": individual_ids,
            }
        log(f"  Skipping group-only Slack IDs from owners_json for {component}")
        break

    github_assignees = _codeowners_matches(workflow_name, codeowners)
    if github_assignees:
        github_users, slack_ids = _resolve_github_users(github_assignees, slack_directory, github_token)
        return {
            "source": "CODEOWNERS",
            "github_assignees": github_users,
            "slack_assignees": slack_ids,
        }

    return {
        "source": "none",
        "github_assignees": [],
        "slack_assignees": [],
    }
