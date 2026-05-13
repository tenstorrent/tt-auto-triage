from __future__ import annotations

import re
from typing import Any

REGRESSION_HANDLING_LABEL = "CI regression handling"
METADATA_START = "<!-- REGRESSION-HANDLING-METADATA-START -->"
METADATA_END = "<!-- REGRESSION-HANDLING-METADATA-END -->"


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


def append_base_markers(body: str, *, workflow_name: str, job_name: str) -> str:
    cleaned = _remove_metadata_block(body)
    lines = [METADATA_START]
    lines += [f"`Regression-handling-workflow: {workflow_name}`", f"`Regression-handling-job-name: {job_name}`"]
    lines.append(METADATA_END)
    return "\n\n".join(part for part in (cleaned, "\n".join(lines)) if part).strip()


def tracked_pairs_from_issues(issues: list[dict[str, Any]]) -> set[tuple[str, str]]:
    tracked: set[tuple[str, str]] = set()
    for issue in issues:
        metadata = _extract_metadata_block(issue.get("body") or "")
        workflow_match = re.search(r"Regression-handling-workflow:\s*`?([^`\n]+)`?", metadata)
        job_match = re.search(r"Regression-handling-job-name:\s*`?([^`\n]+)`?", metadata)
        if workflow_match and job_match:
            tracked.add((workflow_match.group(1).strip(), job_match.group(1).strip()))
    return tracked
