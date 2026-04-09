"""Determine whether a tracked CI job is still failing, resolved, or removed."""

from __future__ import annotations

import base64
import time
from typing import Any

from .helpers import api_get, log

# Job conclusion values from the GitHub Actions API.
_FAILURE = "failure"
_SUCCESS = "success"
_SKIPPED = "skipped"


def _workflow_file_for(workflow_name: str, target_repo: str, token: str | None) -> str | None:
    """Return the filename (not path) of the workflow YAML matching workflow_name.

    GitHub's workflow name is the `name:` field in the YAML, which may differ
    from the filename. We fetch the list of workflows and match by name.
    """
    owner, repo = target_repo.split("/")
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows?per_page=100"
    try:
        data = api_get(url, token)
        for wf in data.get("workflows", []):
            if wf.get("name", "").lower() == workflow_name.lower():
                # path is like ".github/workflows/foo.yaml"
                return wf.get("path", "").split("/")[-1]
    except Exception as exc:
        log(f"  Warning: could not fetch workflow list: {exc}")
    return None


def _fetch_workflow_yaml(workflow_file: str, target_repo: str, token: str | None) -> str:
    """Fetch the raw text of a workflow YAML file from the default branch."""
    owner, repo = target_repo.split("/")
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/.github/workflows/{workflow_file}"
    data = api_get(url, token)
    # Content is base64-encoded.
    return base64.b64decode(data["content"]).decode("utf-8", errors="replace")


def _job_present_in_yaml(job_name: str, yaml_text: str) -> bool:
    """Return True if the job name appears anywhere in the YAML text.

    We check the raw text including comments, because a commented-out job
    means it was disabled (not deleted) and the issue should stay open.
    """
    return job_name in yaml_text


def _get_recent_job_conclusions(
    workflow_name: str,
    job_name: str,
    target_repo: str,
    token: str | None,
    n: int,
) -> list[str]:
    """Return the conclusion strings for job_name across the last n completed runs."""
    owner, repo = target_repo.split("/")

    # Find the workflow ID by name so we can filter runs to this workflow only.
    workflow_file = _workflow_file_for(workflow_name, target_repo, token)
    if not workflow_file:
        log(f"  Warning: workflow '{workflow_name}' not found in {target_repo}")
        return []

    url = (
        f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/"
        f"{workflow_file}/runs?status=completed&per_page={n}"
    )
    try:
        data = api_get(url, token)
    except Exception as exc:
        log(f"  Warning: could not fetch runs for '{workflow_name}': {exc}")
        return []

    runs = data.get("workflow_runs", [])[:n]
    conclusions: list[str] = []

    for run in runs:
        run_id = run["id"]
        jobs_url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/jobs?per_page=100"
        try:
            jobs_data = api_get(jobs_url, token)
            matched = [j for j in jobs_data.get("jobs", []) if j.get("name") == job_name]
            if matched:
                conclusions.append(matched[0].get("conclusion") or "")
            # If not matched, job wasn't in this run -- don't append anything.
        except Exception as exc:
            log(f"  Warning: could not fetch jobs for run {run_id}: {exc}")
        time.sleep(0.2)

    return conclusions


class JobStatus:
    STILL_FAILING = "still_failing"
    RESOLVED = "resolved"
    # Job appears in workflow YAML (possibly as a comment / disabled) but
    # hasn't shown up in recent runs -- treat as disabled, not deleted.
    DISABLED = "disabled"
    # Job name is completely absent from the workflow YAML text.
    REMOVED = "removed"
    # All recent runs passed but we saw at least one "skipped" conclusion,
    # which means the failure may be hidden rather than fixed.
    SKIPPED = "skipped"
    # Could not determine status (API errors, no runs available, etc.).
    UNKNOWN = "unknown"


def check_job_status(
    workflow_name: str,
    job_name: str,
    target_repo: str,
    token: str | None = None,
    consecutive: int = 3,
) -> str:
    """Return a JobStatus constant describing the current state of the job.

    Decision logic:
    1. Fetch the last `consecutive` completed runs.
    2. If any conclusion is "skipped" → SKIPPED (failure may be hidden).
    3. If all conclusions are "failure" → STILL_FAILING.
    4. If all conclusions are "success" → RESOLVED.
    5. If the job produced no conclusions (absent from all recent runs):
       a. Fetch the workflow YAML and check if the job name appears anywhere
          (including comments). If yes → DISABLED. If no → REMOVED.
    6. Mixed conclusions (some pass, some fail) → STILL_FAILING (conservative).
    7. Anything else (empty results, API errors) → UNKNOWN.
    """
    conclusions = _get_recent_job_conclusions(
        workflow_name, job_name, target_repo, token, consecutive
    )

    if not conclusions:
        # Job produced no conclusions in recent runs -- check the YAML.
        log(f"  No recent conclusions for '{job_name}', checking workflow YAML...")
        workflow_file = _workflow_file_for(workflow_name, target_repo, token)
        if not workflow_file:
            return JobStatus.UNKNOWN
        try:
            yaml_text = _fetch_workflow_yaml(workflow_file, target_repo, token)
        except Exception as exc:
            log(f"  Warning: could not fetch workflow YAML: {exc}")
            return JobStatus.UNKNOWN

        if _job_present_in_yaml(job_name, yaml_text):
            # Still in the file (possibly commented/disabled) -- do not close.
            log(f"  '{job_name}' found in YAML (disabled, not deleted) → DISABLED")
            return JobStatus.DISABLED
        else:
            log(f"  '{job_name}' absent from YAML entirely → REMOVED")
            return JobStatus.REMOVED

    if any(c == _SKIPPED for c in conclusions):
        return JobStatus.SKIPPED

    if all(c == _SUCCESS for c in conclusions):
        return JobStatus.RESOLVED

    if all(c == _FAILURE for c in conclusions):
        return JobStatus.STILL_FAILING

    # Mixed -- at least one failure among the recent runs.
    return JobStatus.STILL_FAILING
