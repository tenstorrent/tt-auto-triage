"""Resolve likely owners for failing jobs from pipeline_reorg, owners.json, and CODEOWNERS."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .helpers import log

IGNORE_CODEOWNERS = {"@tenstorrent/codeowner-bypass", "@tenstorrent/metalium-developers-infra"}


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


def _match_codeowners(
    workflow_name: str,
    codeowners: dict[str, list[str]],
) -> list[dict[str, str]]:
    """Try to match workflow name to a CODEOWNERS pattern.

    Looks for .github/workflows/<workflow>.yaml patterns and returns
    matching individual GitHub usernames (not team handles).
    """
    wf_lower = workflow_name.lower().replace(" ", "-").replace("(", "").replace(")", "")
    candidates: list[str] = []
    for pattern, owners in codeowners.items():
        pat_lower = pattern.lower()
        if ".github/workflows/" in pat_lower:
            pat_base = pat_lower.rsplit("/", 1)[-1].replace(".yaml", "").replace(".yml", "").replace("*", "")
            if pat_base and pat_base in wf_lower:
                candidates.extend(owners)
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for gh_user in candidates:
        if gh_user not in seen:
            seen.add(gh_user)
            result.append({"id": f"@{gh_user}", "name": gh_user, "source": "CODEOWNERS"})
    return result


def resolve_owners(
    workflow_name: str,
    job_name: str,
    owners_json: list[dict[str, Any]],
    pipeline_owners: list[dict[str, Any]],
    codeowners: dict[str, list[str]] | None = None,
    agent_suggested: list[str] | None = None,
) -> list[dict[str, str]]:
    """Resolve owners from multiple sources with priority: pipeline_reorg > owners.json > CODEOWNERS > agent."""
    combined = f"{workflow_name} / {job_name}".lower()
    job_lower = job_name.lower()

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
        co_owners = _match_codeowners(workflow_name, codeowners)
        if co_owners:
            return co_owners

    if agent_suggested:
        return [{"id": f"@{u}", "name": u, "source": "agent"} for u in agent_suggested]

    return []
