from __future__ import annotations

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


def append_base_markers(
    body: str,
    *,
    workflow_name: str,
    job_name: str,
    extra_jobs: list[tuple[str, str]] | None = None,
) -> str:
    """Embed auto-triage metadata covering one or more (workflow, job) pairs.

    ``extra_jobs`` lists additional (workflow_name, job_name) pairs that are
    grouped into this single issue so they are not filed again separately.
    """
    cleaned = _remove_metadata_block(body)
    lines = [METADATA_START]
    lines += [f"`Auto-triage-workflow: {workflow_name}`", f"`Auto-triage-job-name: {job_name}`"]
    for wf, jn in (extra_jobs or []):
        lines += [f"`Auto-triage-workflow: {wf}`", f"`Auto-triage-job-name: {jn}`"]
    lines.append(METADATA_END)
    return "\n\n".join(part for part in (cleaned, "\n".join(lines)) if part).strip()


def tracked_pairs_from_issues(issues: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """Extract all (workflow_name, job_name) pairs tracked across all open issues.

    Supports issues that cover multiple grouped jobs (multiple pairs per metadata block).
    """
    tracked: set[tuple[str, str]] = set()
    for issue in issues:
        metadata = _extract_metadata_block(issue.get("body") or "")
        workflows = re.findall(r"Auto-triage-workflow:\s*`?([^`\n]+)`?", metadata)
        jobs = re.findall(r"Auto-triage-job-name:\s*`?([^`\n]+)`?", metadata)
        for wf, jn in zip(workflows, jobs):
            tracked.add((wf.strip(), jn.strip()))
    return tracked
