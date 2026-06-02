from __future__ import annotations

import calendar
import os
import re
import time
from pathlib import Path
from typing import Any, Iterator

from .helpers import gh, log, paginate_api, sanitize_text

SKIP_KEYWORDS: tuple[str, ...] = ("Nightly tt-metal L2 tests",)


def _run_timestamp(run: dict[str, Any]) -> float:
    """Parse a run's GitHub timestamp into epoch seconds; 0 if unparseable."""
    raw = run.get("created_at") or run.get("run_started_at") or ""
    try:
        return calendar.timegm(time.strptime(raw, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return 0.0


def _run_date_iso(run: dict[str, Any]) -> str:
    """Return the ISO-8601 date string from a run, or '' if unavailable."""
    return run.get("created_at") or run.get("run_started_at") or ""


def _build_boundary_info(
    first_failing_run: dict[str, Any] | None,
    last_passing_run: dict[str, Any] | None,
    most_recent_failing_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a dict with temporal boundary fields for a failing job.

    Fields produced (all optional — missing when data is unavailable):
      - first_failing_sha / first_failing_date / first_failing_url
      - last_failing_sha  / last_failing_date  / last_failing_url
      - last_passing_sha  / last_passing_date  / last_passing_url
    """
    info: dict[str, Any] = {}

    if first_failing_run:
        info["first_failing_sha"] = first_failing_run.get("head_sha", "")
        info["first_failing_date"] = _run_date_iso(first_failing_run)
        info["first_failing_url"] = first_failing_run.get("html_url", "")

    if most_recent_failing_runs:
        latest = most_recent_failing_runs[0]
        info["last_failing_sha"] = latest.get("head_sha", "")
        info["last_failing_date"] = _run_date_iso(latest)
        info["last_failing_url"] = latest.get("html_url", "")

    if last_passing_run:
        info["last_passing_sha"] = last_passing_run.get("head_sha", "")
        info["last_passing_date"] = _run_date_iso(last_passing_run)
        info["last_passing_url"] = last_passing_run.get("html_url", "")

    return info


def iter_failing_jobs(
    workflow_data: list[list[Any]],
    target_repo: str,
    consecutive_high_volume: int = 4,
    consecutive_low_volume: int = 2,
    high_volume_runs_per_day: int = 5,
    tracked_pairs: set[tuple[str, str]] | None = None,
) -> Iterator[dict[str, Any]]:
    """Find jobs that have failed in the last N consecutive runs of their workflow.

    The threshold N is adaptive per workflow:
      * If the workflow had MORE than ``high_volume_runs_per_day`` runs on main
        in the last 24h (the artifact is already pre-filtered to main + non-
        manual + completed by the upstream aggregator), require
        ``consecutive_high_volume`` consecutive failures.
      * Otherwise, require ``consecutive_low_volume``.

    Each result dict carries a ``"consecutive"`` key with the threshold that
    was applied for that workflow, so downstream code (e.g. issue drafting)
    can reference the correct number of failing runs.
    """
    token = os.environ.get("GITHUB_TOKEN")
    owner, repo = target_repo.split("/")
    now = time.time()
    one_day_ago = now - 86_400

    for workflow_name, runs in workflow_data:
        if not runs:
            continue
        if any(kw.lower() in str(workflow_name).lower() for kw in SKIP_KEYWORDS):
            log(f"  Skipping '{workflow_name}' (skip keyword)")
            continue

        recent_count = sum(1 for r in runs if _run_timestamp(r) >= one_day_ago)
        if recent_count > high_volume_runs_per_day:
            consecutive = consecutive_high_volume
            volume_label = "high-volume"
        else:
            consecutive = consecutive_low_volume
            volume_label = "low-volume"
        log(
            f"  '{workflow_name}': {recent_count} run(s) in last 24h "
            f"({volume_label}) -> requires {consecutive} consecutive failure(s)"
        )

        sorted_runs = sorted(runs, key=_run_timestamp, reverse=True)[:consecutive]
        if len(sorted_runs) < consecutive:
            log(
                f"  '{workflow_name}': skipped before candidate creation "
                f"(only {len(sorted_runs)} run(s), need {consecutive})"
            )
            continue
        if not all(run.get("conclusion") == "failure" for run in sorted_runs):
            log(f"  '{workflow_name}': skipped before candidate creation (latest {consecutive} runs are not all failures)")
            continue

        log(f"  '{workflow_name}': {consecutive} consecutive failures, fetching jobs...")
        run_failed_jobs: dict[int, dict[str, str]] = {}
        for run in sorted_runs:
            run_id = run.get("id")
            if not run_id:
                continue
            try:
                all_jobs = paginate_api(
                    f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/jobs?per_page=100",
                    "jobs",
                    token,
                )
            except Exception as exc:
                log(f"  Warning: skipping run {run_id} for '{workflow_name}' due to API error ({type(exc).__name__}): {exc}")
                continue
            run_failed_jobs[run_id] = {
                job["name"]: job.get("html_url", "") for job in all_jobs if job.get("conclusion") == "failure"
            }
            time.sleep(0.3)

        if len(run_failed_jobs) < consecutive:
            log(
                f"  '{workflow_name}': skipped before candidate creation "
                f"({len(run_failed_jobs)}/{consecutive} runs had job data)"
            )
            continue

        run_ids = list(run_failed_jobs.keys())
        common_jobs = set(run_failed_jobs[run_ids[0]])
        for run_id in run_ids[1:]:
            common_jobs &= set(run_failed_jobs[run_id])
        if not common_jobs:
            log(f"  '{workflow_name}': skipped before candidate creation (no common failing jobs across runs)")
            continue

        # ── Temporal boundary: first failing & last passing run ────────
        # Walk full history (newest→oldest) to find the current contiguous
        # failure streak and the most-recent success before it.
        all_sorted = sorted(runs, key=_run_timestamp, reverse=True)
        first_failing_run: dict[str, Any] | None = None
        last_passing_run: dict[str, Any] | None = None
        for run in all_sorted:
            conclusion = run.get("conclusion")
            if conclusion == "failure":
                first_failing_run = run  # move boundary back into the streak
            elif conclusion == "success":
                if first_failing_run is not None:
                    # First success after the failure streak — this is the
                    # boundary between the current streak and older history.
                    last_passing_run = run
                    break
                # Most-recent run is a success — no current failure streak.
                break

        boundary_info = _build_boundary_info(
            first_failing_run, last_passing_run, sorted_runs,
        )

        for job_name in sorted(common_jobs):
            if tracked_pairs and (workflow_name, job_name) in tracked_pairs:
                log(f"  Skipping already-tracked job: {workflow_name} / {job_name}")
                continue
            yield {
                "workflow_name": workflow_name,
                "job_name": job_name,
                "consecutive": consecutive,
                "job_urls": [run_failed_jobs[run_id].get(job_name, "") for run_id in run_ids],
                "run_urls": [run.get("html_url", "") for run in sorted_runs],
                **boundary_info,
            }

    # Generator may be consumed lazily; caller may stop early.


def find_failing_jobs(
    workflow_data: list[list[Any]],
    target_repo: str,
    consecutive_high_volume: int = 4,
    consecutive_low_volume: int = 2,
    high_volume_runs_per_day: int = 5,
    tracked_pairs: set[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Compatibility wrapper returning a materialized list."""
    jobs = list(
        iter_failing_jobs(
            workflow_data,
            target_repo,
            consecutive_high_volume=consecutive_high_volume,
            consecutive_low_volume=consecutive_low_volume,
            high_volume_runs_per_day=high_volume_runs_per_day,
            tracked_pairs=tracked_pairs,
        )
    )
    log(f"  Found {len(jobs)} deterministically-failing jobs")
    return jobs


def _parse_job_url(url: str) -> tuple[int, int]:
    match = re.search(r"/runs/(\d+)/job/(\d+)", url)
    if not match:
        raise ValueError(f"Cannot parse job URL: {url}")
    return int(match.group(1)), int(match.group(2))


def _sanitize(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:80]


def download_job_logs(
    jobs: list[dict[str, Any]],
    target_repo: str,
    logs_dir: Path,
) -> list[dict[str, Any]]:
    token = os.environ.get("GITHUB_TOKEN", "")
    logs_dir.mkdir(parents=True, exist_ok=True)
    enriched: list[dict[str, Any]] = []

    for job in jobs:
        workflow_name = _sanitize(job["workflow_name"])
        job_name = _sanitize(job["job_name"])
        log_paths: list[str] = []
        run_log_entries: list[dict[str, Any]] = []

        for index, job_url in enumerate(job.get("job_urls", []), start=1):
            if not job_url:
                continue
            out_path = logs_dir / f"{workflow_name}__{job_name}__run{index}.txt"
            try:
                run_id, job_id = _parse_job_url(job_url)
                text = gh(
                    "run",
                    "view",
                    str(run_id),
                    f"--repo={target_repo}",
                    "--job",
                    str(job_id),
                    "--log-failed",
                    token=token,
                    timeout=60,
                )
                if not text.strip():
                    text = gh(
                        "run",
                        "view",
                        str(run_id),
                        f"--repo={target_repo}",
                        "--job",
                        str(job_id),
                        "--log",
                        token=token,
                        timeout=60,
                    )
                out_path.write_text(sanitize_text(text), encoding="utf-8")
                log_paths.append(str(out_path))
                run_log_entries.append(
                    {
                        "run_index": index,
                        "job_url": job_url,
                        "log_path": str(out_path),
                    }
                )
            except Exception as exc:
                log(f"  Warning: failed to download log for {job_url}: {exc}")
                out_path.write_text(f"(log download failed: {exc})", encoding="utf-8")
                log_paths.append(str(out_path))
                run_log_entries.append(
                    {
                        "run_index": index,
                        "job_url": job_url,
                        "log_path": str(out_path),
                    }
                )

        enriched.append({**job, "log_paths": log_paths, "run_log_entries": run_log_entries})
        log(f"  Downloaded {len(log_paths)} logs for {job['job_name']}")

    return enriched
