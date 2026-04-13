from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from .helpers import api_get, gh, log, sanitize_text

SKIP_KEYWORDS: tuple[str, ...] = ("sanity", "Nightly tt-metal L2 tests")


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

        sorted_runs = sorted(
            runs,
            key=lambda run: run.get("created_at", "") or run.get("run_started_at", ""),
            reverse=True,
        )[:consecutive]
        if len(sorted_runs) < consecutive:
            continue
        if not all(run.get("conclusion") == "failure" for run in sorted_runs):
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
                url = (
                    f"https://api.github.com/repos/{owner}/{repo}/actions/runs/"
                    f"{run_id}/jobs?per_page=100&page={page}"
                )
                data = api_get(url, token)
                batch = data.get("jobs", [])
                all_jobs.extend(batch)
                if len(batch) < 100:
                    break
                page += 1
            run_failed_jobs[run_id] = {
                job["name"]: job.get("html_url", "")
                for job in all_jobs
                if job.get("conclusion") == "failure"
            }
            time.sleep(0.3)

        if len(run_failed_jobs) < consecutive:
            continue

        run_ids = list(run_failed_jobs.keys())
        common_jobs = set(run_failed_jobs[run_ids[0]])
        for run_id in run_ids[1:]:
            common_jobs &= set(run_failed_jobs[run_id])

        for job_name in sorted(common_jobs):
            if tracked_pairs and (workflow_name, job_name) in tracked_pairs:
                log(f"  Skipping already-tracked job: {workflow_name} / {job_name}")
                continue
            results.append(
                {
                    "workflow_name": workflow_name,
                    "job_name": job_name,
                    "job_urls": [run_failed_jobs[run_id].get(job_name, "") for run_id in run_ids],
                    "run_urls": [run.get("html_url", "") for run in sorted_runs],
                }
            )

    log(f"  Found {len(results)} deterministically-failing jobs")
    return results


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
            except Exception as exc:
                log(f"  Warning: failed to download log for {job_url}: {exc}")
                out_path.write_text(f"(log download failed: {exc})", encoding="utf-8")
                log_paths.append(str(out_path))

        enriched.append({**job, "log_paths": log_paths})
        log(f"  Downloaded {len(log_paths)} logs for {job['job_name']}")

    return enriched
