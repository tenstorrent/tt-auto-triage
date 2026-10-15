"""Search for the PR that most likely fixed a previously-failing CI job."""

from __future__ import annotations

import re
from typing import Any

from .helpers import gh, log


def _workflow_file_guess(workflow_name: str) -> str:
    """Best-effort guess at the workflow filename from its display name.

    GitHub doesn't expose the filename in issue bodies, so we normalise the
    name the same way GitHub does for auto-generated workflow names.
    """
    return re.sub(r"[^a-z0-9]+", "-", workflow_name.lower()).strip("-") + ".yaml"


def find_fix_pr(
    workflow_name: str,
    job_name: str,
    issue_created_at: str,
    target_repo: str,
    token: str | None = None,
) -> dict[str, str] | None:
    """Search merged PRs for the one that most likely fixed this job.

    Returns {"url": ..., "title": ...} for the best candidate, or None.

    Search strategy (in order):
    1. Merged PRs since issue_created_at that modified the workflow YAML file.
    2. Merged PRs since issue_created_at whose title or body mentions the job name.
    We return the most recently merged PR across both result sets.
    """
    wf_file = _workflow_file_guess(workflow_name)
    date_filter = f"merged:>={issue_created_at[:10]}"  # ISO date prefix

    candidates: list[dict[str, Any]] = []

    # Strategy 1: PRs that touched the workflow file.
    try:
        raw = gh(
            "pr", "list",
            f"--repo={target_repo}",
            "--state=merged",
            "--limit=20",
            f"--search={date_filter} {wf_file} in:files",
            "--json=number,title,url,mergedAt",
            token=token,
        )
        import json
        candidates.extend(json.loads(raw))
        log(f"  Workflow-file search found {len(candidates)} candidate PR(s)")
    except Exception as exc:
        log(f"  Warning: workflow-file PR search failed: {exc}")

    # Strategy 2: PRs that mention the job name in title or body.
    # Use a quoted search to handle job names with spaces.
    safe_name = job_name.replace('"', "")[:100]
    try:
        raw = gh(
            "pr", "list",
            f"--repo={target_repo}",
            "--state=merged",
            "--limit=20",
            f'--search={date_filter} "{safe_name}" in:title,body',
            "--json=number,title,url,mergedAt",
            token=token,
        )
        import json
        by_name = json.loads(raw)
        # Deduplicate by PR number.
        existing_numbers = {c.get("number") for c in candidates}
        for pr in by_name:
            if pr.get("number") not in existing_numbers:
                candidates.append(pr)
        log(f"  Job-name search added {len(by_name)} more candidate PR(s)")
    except Exception as exc:
        log(f"  Warning: job-name PR search failed: {exc}")

    if not candidates:
        return None

    # Pick the most recently merged candidate.
    candidates.sort(key=lambda p: p.get("mergedAt") or "", reverse=True)
    best = candidates[0]
    return {"url": best.get("url", ""), "title": best.get("title", "")}
