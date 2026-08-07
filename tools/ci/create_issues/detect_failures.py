from __future__ import annotations

import calendar
import os
import re
import time
from pathlib import Path
from typing import Any, Iterator

from .helpers import api_get, gh, log, paginate_api, sanitize_text

SKIP_KEYWORDS: tuple[str, ...] = ()


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
    oldest_failing_run: dict[str, Any] | None,
    newest_failing_run: dict[str, Any] | None,
    last_passing_run: dict[str, Any] | None,
    oldest_failing_job_url: str = "",
    newest_failing_job_url: str = "",
) -> dict[str, Any]:
    """Build a dict with temporal boundary fields for a failing job.

    The first/last failing boundaries reference the oldest and newest runs we
    actually analyzed and confirmed failing with the same error (i.e. the runs
    quoted in the issue) — NOT a streak start inferred from full history, which
    may have failed for a different reason. Their URLs point to the specific
    failing JOB, not the run.

    Fields produced (all optional — missing when data is unavailable):
      - first_failing_sha / first_failing_date / first_failing_url (job URL)
      - last_failing_sha  / last_failing_date  / last_failing_url  (job URL)
      - last_passing_sha  / last_passing_date  / last_passing_url   (run URL)
    """
    info: dict[str, Any] = {}

    if oldest_failing_run:
        info["first_failing_sha"] = oldest_failing_run.get("head_sha", "")
        info["first_failing_date"] = _run_date_iso(oldest_failing_run)
        info["first_failing_url"] = oldest_failing_job_url

    if newest_failing_run:
        info["last_failing_sha"] = newest_failing_run.get("head_sha", "")
        info["last_failing_date"] = _run_date_iso(newest_failing_run)
        info["last_failing_url"] = newest_failing_job_url

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

        # ── Temporal boundary: most-recent passing run ────────────────
        # Walk full history (newest→oldest) to find the most-recent success
        # before the current contiguous failure streak. The first/last failing
        # boundaries are taken from the analyzed runs below, not from the
        # streak start (which may have failed for a different reason).
        all_sorted = sorted(runs, key=_run_timestamp, reverse=True)
        in_streak = False
        last_passing_run: dict[str, Any] | None = None
        for run in all_sorted:
            conclusion = run.get("conclusion")
            if conclusion == "failure":
                in_streak = True  # we are inside the current failure streak
            elif conclusion == "success":
                if in_streak:
                    # First success after the failure streak — the boundary
                    # between the current streak and older history.
                    last_passing_run = run
                # Most-recent run is a success (or boundary found) — stop.
                break

        # run_ids preserves sorted_runs order (newest→oldest), so index 0 is
        # the newest analyzed failing run and index -1 the oldest.
        oldest_failing_run = sorted_runs[-1]
        newest_failing_run = sorted_runs[0]

        for job_name in sorted(common_jobs):
            if tracked_pairs and (workflow_name, job_name) in tracked_pairs:
                log(f"  Skipping already-tracked job: {workflow_name} / {job_name}")
                continue
            job_urls = [run_failed_jobs[run_id].get(job_name, "") for run_id in run_ids]
            boundary_info = _build_boundary_info(
                oldest_failing_run,
                newest_failing_run,
                last_passing_run,
                oldest_failing_job_url=job_urls[-1] if job_urls else "",
                newest_failing_job_url=job_urls[0] if job_urls else "",
            )
            yield {
                "workflow_name": workflow_name,
                "job_name": job_name,
                "consecutive": consecutive,
                "job_urls": job_urls,
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


def _newest_completed_main_run(
    owner: str,
    repo: str,
    workflow_id: int,
    token: str | None,
) -> dict[str, Any] | None:
    """Return the newest completed, non-manual run on main for a workflow.

    Mirrors the upstream aggregator's filtering (main + non-manual + completed)
    so the live re-check compares like-for-like with the snapshot.
    """
    data = api_get(
        f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/"
        f"{workflow_id}/runs?branch=main&status=completed&per_page=30",
        token,
    )
    # The API returns runs newest-first; take the newest non-manual one.
    for run in data.get("workflow_runs", []):
        if run.get("event") == "workflow_dispatch":
            continue
        return run
    return None


def streak_still_failing(
    job: dict[str, Any],
    target_repo: str,
    token: str | None = None,
) -> tuple[bool, str]:
    """Live re-check that the candidate's job still fails right now.

    The detector works off a periodic snapshot artifact that can be a couple of
    hours stale. A newer run may have already passed (breaking the failure
    streak) by the time we file. This confirms the streak is still intact
    against live GitHub data before creating an issue.

    Returns ``(still_failing, reason)``. ``reason`` explains why the streak
    looks broken when ``still_failing`` is False. On any API error it fails
    OPEN (returns ``True``) so a transient hiccup never blocks an otherwise
    valid issue — the snapshot-based detection already passed.
    """
    if token is None:
        token = os.environ.get("GITHUB_TOKEN")
    owner, repo = target_repo.split("/")
    job_name = job.get("job_name", "")
    newest_job_url = next((u for u in job.get("job_urls", []) if u), "")
    if not newest_job_url:
        return True, ""  # nothing to verify against; don't block

    try:
        newest_analyzed_run_id, _ = _parse_job_url(newest_job_url)
        run_detail = api_get(
            f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{newest_analyzed_run_id}",
            token,
        )
        workflow_id = run_detail.get("workflow_id")
        if not workflow_id:
            return True, ""

        newest_live = _newest_completed_main_run(owner, repo, workflow_id, token)
        if not newest_live:
            return True, ""

        live_run_id = newest_live.get("id")
        # No newer completed run than the one we analyzed → streak unchanged.
        if live_run_id == newest_analyzed_run_id:
            return True, ""

        # A newer completed run exists. Is the job still failing in it?
        jobs = paginate_api(
            f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{live_run_id}/jobs?per_page=100",
            "jobs",
            token,
        )
        if any(j.get("name") == job_name and j.get("conclusion") == "failure" for j in jobs):
            return True, ""

        live_url = newest_live.get("html_url", str(live_run_id))
        if any(j.get("name") == job_name for j in jobs):
            return False, (
                f"streak broken: job passed in newer run {live_url} "
                f"(snapshot's newest failing run was stale)"
            )
        return False, (
            f"streak broken: job absent from newer run {live_url} "
            f"(snapshot was stale)"
        )
    except Exception as exc:
        log(
            f"  Warning: freshness re-check failed "
            f"({type(exc).__name__}): {exc}; proceeding"
        )
        return True, ""


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
