"""Resolve likely owners for failing jobs from pipeline_reorg and owners.json."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .helpers import log


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


def resolve_owners(
    workflow_name: str,
    job_name: str,
    owners_json: list[dict[str, Any]],
    pipeline_owners: list[dict[str, Any]],
) -> list[dict[str, str]]:
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
    return []
