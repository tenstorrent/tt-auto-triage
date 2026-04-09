"""Detect jobs that failed in N consecutive runs and download their logs."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from .helpers import api_get, gh, log

SKIP_KEYWORDS: tuple[str, ...] = ("sanity", "Nightly tt-metal L2 tests",)


def find_failing_jobs(
    workflow_data: list[list[Any]],
    target_repo: str,
    consecutive: int = 3,
    tracked_pairs: set[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    token = os.environ.get("GITHUB_TOKEN")
    owner, repo = target_repo.split("/")
    results: list[dict[str, Any]] = []

    for workflow_name, runs in workflow_data:
        if not runs:
            continue
        if any(kw.lower() in str(workflow_name).lower() for kw in SKIP_KEYWORDS):
            log(f"  Skipping '{workflow_name}' (skip keyword)")
            continue

        # Take only the N most recent runs. We check the recency sort before
        # the failure check so we don't accidentally count stale old failures
        # if a workflow was fixed and broke again.
        sorted_runs = sorted(
            runs,
            key=lambda r: r.get("created_at", "") or r.get("run_started_at", ""),
            reverse=True,
        )[:consecutive]

        if len(sorted_runs) < consecutive:
            # Not enough history yet -- skip rather than false-positive.
            continue
        if not all(r.get("conclusion") == "failure" for r in sorted_runs):
            continue

        log(f"  '{workflow_name}': {consecutive} consecutive failures, fetching jobs...")
        run_failed_jobs: dict[int, dict[str, str]] = {}
        for run in sorted_runs:
            run_id = run.get("id")
            if not run_id:
                continue
            all_jobs: list[dict[str, Any]] = []
            page = 1
            while True:
                url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/jobs?per_page=100&page={page}"
                data = api_get(url, token)
                batch = data.get("jobs", [])
                all_jobs.extend(batch)
                if len(batch) < 100:
                    break
                page += 1
            failed = {
                j["name"]: j.get("html_url", "")
                for j in all_jobs
                if j.get("conclusion") == "failure"
            }
            run_failed_jobs[run_id] = failed
            time.sleep(0.3)

        if len(run_failed_jobs) < consecutive:
            continue

        all_run_ids = list(run_failed_jobs.keys())
        # Intersection across all N runs: only jobs that failed in *every* run
        # are considered deterministic. A job that failed in 2/3 runs is not
        # reported -- it could be flaky rather than broken.
        common_jobs = set(run_failed_jobs[all_run_ids[0]])
        for rid in all_run_ids[1:]:
            common_jobs &= set(run_failed_jobs[rid])

        for job_name in sorted(common_jobs):
            if tracked_pairs and (workflow_name, job_name) in tracked_pairs:
                log(f"  Skipping already-tracked job: {workflow_name} / {job_name}")
                continue
            job_urls = [run_failed_jobs[rid].get(job_name, "") for rid in all_run_ids]
            run_urls = [r.get("html_url", "") for r in sorted_runs]
            results.append({
                "workflow_name": workflow_name,
                "job_name": job_name,
                "job_urls": job_urls,
                "run_urls": run_urls,
            })

    log(f"  Found {len(results)} deterministically-failing jobs")
    return results


def _parse_job_url(url: str) -> tuple[int, int]:
    """Extract (run_id, job_id) from a GitHub Actions job URL."""
    m = re.search(r"/runs/(\d+)/job/(\d+)", url)
    if not m:
        raise ValueError(f"Cannot parse job URL: {url}")
    return int(m.group(1)), int(m.group(2))


def _sanitize(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:80]


def download_job_logs(
    jobs: list[dict[str, Any]],
    target_repo: str,
    logs_dir: Path,
) -> list[dict[str, Any]]:
    """Download failed-job logs for each job. Returns enriched job dicts with log_paths."""
    token = os.environ.get("GITHUB_TOKEN", "")
    logs_dir.mkdir(parents=True, exist_ok=True)
    enriched: list[dict[str, Any]] = []

    for job in jobs:
        wf = _sanitize(job["workflow_name"])
        jn = _sanitize(job["job_name"])
        log_paths: list[str] = []

        for idx, job_url in enumerate(job.get("job_urls", []), start=1):
            if not job_url:
                continue
            out_path = logs_dir / f"{wf}__{jn}__run{idx}.txt"
            try:
                run_id, job_id = _parse_job_url(job_url)
                text = gh(
                    "run", "view", str(run_id),
                    f"--repo={target_repo}", "--job", str(job_id),
                    "--log-failed", token=token, timeout=60,
                )
                if not text.strip():
                    # --log-failed returns nothing when the job runner itself
                    # crashed before producing step-level annotations; fall back
                    # to the full log so the agent still has something to read.
                    text = gh(
                        "run", "view", str(run_id),
                        f"--repo={target_repo}", "--job", str(job_id),
                        "--log", token=token, timeout=60,
                    )
                out_path.write_text(text, encoding="utf-8")
                log_paths.append(str(out_path))
            except Exception as exc:
                log(f"  Warning: failed to download log for {job_url}: {exc}")
                out_path.write_text(f"(log download failed: {exc})", encoding="utf-8")
                log_paths.append(str(out_path))

        enriched.append({**job, "log_paths": log_paths})
        log(f"  Downloaded {len(log_paths)} logs for {job['job_name']}")

    return enriched
