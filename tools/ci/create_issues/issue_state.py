from __future__ import annotations

import json
import re
from typing import Any

AUTO_TRIAGE_LABEL = "CI auto triage"
METADATA_START = "<!-- AUTO-TRIAGE-METADATA-START -->"
METADATA_END = "<!-- AUTO-TRIAGE-METADATA-END -->"


def _extract_metadata_block(body: str) -> str:
    match = re.search(
        rf"{re.escape(METADATA_START)}\n?(.*?)\n?{re.escape(METADATA_END)}",
        body,
        re.DOTALL,
    )
    return match.group(1).strip() if match else ""


def _remove_metadata_block(body: str) -> str:
    cleaned = re.sub(
        rf"\n?{re.escape(METADATA_START)}\n?.*?\n?{re.escape(METADATA_END)}",
        "",
        body,
        flags=re.DOTALL,
    )
    return cleaned.strip()


def append_base_markers(body: str, *, workflow_name: str, job_name: str, fingerprint: str, suggested_owners: list[str] | None = None) -> str:
    cleaned = _remove_metadata_block(body)
    lines = [METADATA_START]
    if fingerprint:
        lines.append(f"`Auto-triage-fingerprint: {fingerprint}`")
    lines += [f"`Auto-triage-workflow: {workflow_name}`", f"`Auto-triage-job-name: {job_name}`"]
    if suggested_owners:
        lines.append(f"`Auto-triage-suggested-owners: {json.dumps(suggested_owners, separators=(',', ':'))}`")
    lines.append(METADATA_END)
    return "\n\n".join(part for part in (cleaned, "\n".join(lines)) if part).strip()


def tracked_pairs_from_issues(issues: list[dict[str, Any]]) -> set[tuple[str, str]]:
    tracked: set[tuple[str, str]] = set()
    for issue in issues:
        metadata = _extract_metadata_block(issue.get("body") or "")
        workflow_match = re.search(r"Auto-triage-workflow:\s*`?([^`\n]+)`?", metadata)
        job_match = re.search(r"Auto-triage-job-name:\s*`?([^`\n]+)`?", metadata)
        if workflow_match and job_match:
            tracked.add((workflow_match.group(1).strip(), job_match.group(1).strip()))
    return tracked
