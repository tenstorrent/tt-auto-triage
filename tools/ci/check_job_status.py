"""Determine whether a tracked CI job is still failing, resolved, or removed."""

from __future__ import annotations

import base64
import time
from datetime import datetime, timezone
from typing import Any

from .helpers import api_get, log

# Job conclusion values from the GitHub Actions API.
_FAILURE = "failure"
_SUCCESS = "success"
_SKIPPED = "skipped"


def workflow_file_for(workflow_name: str, target_repo: str, token: str | None) -> str | None:
    """Return the filename (not path) of the workflow YAML matching workflow_name.

    GitHub's workflow name is the `name:` field in the YAML, which may differ
    from the filename. Paginates through all workflows -- repos like tt-metal
    have 200+ and a single page misses most of them.
    """
    owner, repo = target_repo.split("/")
    page = 1
    try:
        while True:
            url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows?per_page=100&page={page}"
            data = api_get(url, token)
            workflows = data.get("workflows", [])
            for wf in workflows:
                if wf.get("name", "").lower() == workflow_name.lower():
                    # path is like ".github/workflows/foo.yaml"
                    return wf.get("path", "").split("/")[-1]
            if len(workflows) < 100:
                break
            page += 1
    except Exception as exc:
        log(f"  Warning: could not fetch workflow list: {exc}")
    return None


def fetch_workflow_yaml(workflow_file: str, target_repo: str, token: str | None) -> str:
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


def _resolve_workflow_file(
    workflow_name: str,
    target_repo: str,
    token: str | None,
    workflow_file_hint: str | None,
) -> str | None:
    """Return the workflow filename, using hint directly if provided."""
    if workflow_file_hint:
        # The hint is either a full path like ".github/workflows/foo.yaml" or
        # just the filename.  We only need the filename for API calls.
        return workflow_file_hint.split("/")[-1]
    return workflow_file_for(workflow_name, target_repo, token)


def get_runs_per_day(
    workflow_name: str,
    target_repo: str,
    token: str | None,
    workflow_file_hint: str | None = None,
    lookback_days: int = 7,
) -> float:
    """Return the average number of completed workflow runs per calendar day.

    Fetches the last `lookback_days` * 5 runs (capped at 100) and divides the
    count by the span between the newest and oldest run, in days.  Returns 0.0
    if there is insufficient data to compute a rate.
    """
    owner, repo = target_repo.split("/")
    wf_file = _resolve_workflow_file(workflow_name, target_repo, token, workflow_file_hint)
    if not wf_file:
        return 0.0

    per_page = min(lookback_days * 5, 100)
    url = (
        f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/"
        f"{wf_file}/runs?status=completed&per_page={per_page}"
    )
    try:
        data = api_get(url, token)
    except Exception as exc:
        log(f"  Warning: could not fetch runs for frequency estimate: {exc}")
        return 0.0

    runs = data.get("workflow_runs", [])
    if len(runs) < 2:
        return 0.0

    def _parse_ts(run: dict[str, Any]) -> datetime | None:
        raw = run.get("created_at") or run.get("run_started_at")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None

    timestamps = [t for r in runs if (t := _parse_ts(r)) is not None]
    if len(timestamps) < 2:
        return 0.0

    newest = max(timestamps)
    oldest = min(timestamps)
    span_days = (newest - oldest).total_seconds() / 86400.0
    if span_days < 0.1:
        return 0.0

    return len(timestamps) / span_days


def get_dynamic_threshold(runs_per_day: float) -> int:
    """Return the number of consecutive runs required to confirm a change.

    Thresholds:
      < 5 runs/day  → 2  (fewer data points per day, lower bar)
      >= 5 runs/day → 5  (many runs/day, require more evidence)
    """
    if runs_per_day >= 5.0:
        return 5
    return 2


def _get_recent_job_conclusions(
    workflow_name: str,
    job_name: str,
    target_repo: str,
    token: str | None,
    n: int,
    workflow_file_hint: str | None = None,
) -> list[str]:
    """Return the conclusion strings for job_name across the last n completed runs."""
    owner, repo = target_repo.split("/")

    # Find the workflow file — use hint if available to skip the name lookup.
    workflow_file = _resolve_workflow_file(workflow_name, target_repo, token, workflow_file_hint)
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


def get_most_recent_run_id(
    workflow_name: str,
    target_repo: str,
    token: str | None,
    workflow_file_hint: str | None = None,
) -> int | None:
    """Return the run ID of the most recently completed run for this workflow."""
    owner, repo = target_repo.split("/")
    wf_file = _resolve_workflow_file(workflow_name, target_repo, token, workflow_file_hint)
    if not wf_file:
        return None
    url = (
        f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/"
        f"{wf_file}/runs?status=completed&per_page=1"
    )
    try:
        data = api_get(url, token)
        runs = data.get("workflow_runs", [])
        return runs[0]["id"] if runs else None
    except Exception as exc:
        log(f"  Warning: could not fetch most recent run: {exc}")
        return None


def get_recent_failing_run_jobs(
    workflow_name: str,
    job_name: str,
    target_repo: str,
    token: str | None,
    n: int,
    workflow_file_hint: str | None = None,
) -> list[dict[str, Any]]:
    """Return metadata for the last n runs where job_name concluded 'failure'.

    Each entry has: run_id, job_id, job_url.
    Used by lifecycle.py to download current failing logs for the agent to
    compare against the original error signature in the issue body.
    """
    owner, repo = target_repo.split("/")
    wf_file = _resolve_workflow_file(workflow_name, target_repo, token, workflow_file_hint)
    if not wf_file:
        return []

    url = (
        f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/"
        f"{wf_file}/runs?status=completed&per_page={n * 3}"
    )
    try:
        data = api_get(url, token)
    except Exception as exc:
        log(f"  Warning: could not fetch runs for failing-log lookup: {exc}")
        return []

    results: list[dict[str, Any]] = []
    for run in data.get("workflow_runs", []):
        if len(results) >= n:
            break
        run_id = run["id"]
        jobs_url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/jobs?per_page=100"
        try:
            jobs_data = api_get(jobs_url, token)
            for j in jobs_data.get("jobs", []):
                if j.get("name") == job_name and j.get("conclusion") == _FAILURE:
                    results.append({
                        "run_id": run_id,
                        "job_id": j["id"],
                        "job_url": j.get("html_url", ""),
                    })
                    break
        except Exception as exc:
            log(f"  Warning: could not fetch jobs for run {run_id}: {exc}")
        time.sleep(0.2)

    return results


def get_recent_passing_runs(
    workflow_name: str,
    job_name: str,
    target_repo: str,
    token: str | None,
    n: int,
    workflow_file_hint: str | None = None,
) -> list[dict[str, Any]]:
    """Return metadata for the last n runs where job_name concluded 'success'.

    Each entry has: run_id, job_id, job_url.
    Used by lifecycle.py to download logs for the agent to analyze.
    """
    owner, repo = target_repo.split("/")
    wf_file = _resolve_workflow_file(workflow_name, target_repo, token, workflow_file_hint)
    if not wf_file:
        return []

    url = (
        f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/"
        f"{wf_file}/runs?status=completed&per_page={n * 3}"  # fetch more to find n passing
    )
    try:
        data = api_get(url, token)
    except Exception as exc:
        log(f"  Warning: could not fetch runs for passing-log lookup: {exc}")
        return []

    results: list[dict[str, Any]] = []
    for run in data.get("workflow_runs", []):
        if len(results) >= n:
            break
        run_id = run["id"]
        jobs_url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/jobs?per_page=100"
        try:
            jobs_data = api_get(jobs_url, token)
            for j in jobs_data.get("jobs", []):
                if j.get("name") == job_name and j.get("conclusion") == _SUCCESS:
                    results.append({
                        "run_id": run_id,
                        "job_id": j["id"],
                        "job_url": j.get("html_url", ""),
                    })
                    break
        except Exception as exc:
            log(f"  Warning: could not fetch jobs for run {run_id}: {exc}")
        time.sleep(0.2)

    return results


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
    workflow_file_hint: str | None = None,
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
        workflow_name, job_name, target_repo, token, consecutive,
        workflow_file_hint=workflow_file_hint,
    )

    if not conclusions:
        # Job produced no conclusions in recent runs -- check the YAML.
        log(f"  No recent conclusions for '{job_name}', checking workflow YAML...")
        workflow_file = _resolve_workflow_file(workflow_name, target_repo, token, workflow_file_hint)
        if not workflow_file:
            return JobStatus.UNKNOWN
        try:
            yaml_text = fetch_workflow_yaml(workflow_file, target_repo, token)
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
