from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from .helpers import gh, log, paginate_api, sanitize_text

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
            all_jobs = paginate_api(
                f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/jobs?per_page=100",
                "jobs",
                token,
            )
            run_failed_jobs[run_id] = {
                job["name"]: job.get("html_url", "") for job in all_jobs if job.get("conclusion") == "failure"
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



# ---------------------------------------------------------------------------
# Deduplication: group jobs with similar error signatures
# ---------------------------------------------------------------------------
import difflib as _difflib

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_TIMESTAMP_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z\s*", re.MULTILINE)

_ERROR_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.MULTILINE)
    for p in (
        r"TT_FATAL\b[^\n]{0,300}",
        r"SIGABRT[^\n]{0,200}",
        r"Segmentation fault[^\n]{0,200}",
        r"AssertionError:[^\n]{0,200}",
        r"RuntimeError:[^\n]{0,200}",
        # pytest: "FAILED path::test - ErrorType: message"
        r"FAILED\s+\S+::[^\n]{0,200}",
        # pytest short: standalone "FAILED" line followed by error class
        r"(?:^|\s)FAILED\s+[^\n]{5,200}",
        # Python exceptions
        r"(?:TypeError|ValueError|KeyError|AttributeError|ImportError|OSError|IOError):[^\n]{0,200}",
        # Performance regression
        r"(?:performance|regression|exceeded|threshold|inference.?time)[^\n]{0,200}",
        # Generic "Error: ..." at start of content
        r"Error:[^\n]{0,200}",
    )
)


def _extract_error_signature(log_text: str) -> str:
    """Extract and normalise the key error line from a log (no third-party deps).

    Strips ANSI escape codes and GitHub Actions timestamp prefixes before matching.
    """
    # Strip ANSI colour codes (pytest wraps FAILED/PASSED in colour sequences)
    clean = _ANSI_ESCAPE.sub("", log_text)
    # Strip GitHub Actions per-line timestamps
    clean = _TIMESTAMP_PREFIX.sub("", clean)

    for pat in _ERROR_PATTERNS:
        m = pat.search(clean)
        if m:
            sig = m.group(0)
            # Strip volatile parts: file paths, line numbers, function names
            sig = re.sub(r"\S+\.(cpp|h|py|cc|cxx|hpp|c):\d+", "", sig)
            sig = re.sub(r" @ \S+", "", sig)
            sig = re.sub(r" in \w+:", "", sig)
            # Strip test node IDs (path::test[params]) to keep the error type
            sig = re.sub(r"\S+::\S+\[[^\]]*\]", "", sig)
            sig = re.sub(r"\s+", " ", sig).strip()
            return sig
    return ""


def _error_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return _difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def group_similar_jobs(
    jobs: list[dict[str, Any]],
    threshold: float = 0.65,
) -> list[list[dict[str, Any]]]:
    """Group jobs with similar error signatures to avoid filing duplicate issues.

    Uses stdlib ``difflib`` only — no third-party dependencies.
    Returns a list of groups; each group is a non-empty list of job dicts.
    The first entry in each group is the *primary* job (drives issue creation).
    """
    signatures: list[str] = []
    for job in jobs:
        sig = ""
        for log_path in job.get("log_paths", []):
            try:
                text = Path(log_path).read_text(errors="replace")[:100_000]
                sig = _extract_error_signature(text)
                if sig:
                    break
            except Exception:
                pass
        signatures.append(sig)
        short = sig[:80] if sig else "(no signature)"
        log(f"    Sig [{job['job_name']!r:.40}]: {short}")

    visited: set[int] = set()
    groups: list[list[dict[str, Any]]] = []
    for i in range(len(jobs)):
        if i in visited:
            continue
        group = [i]
        visited.add(i)
        if signatures[i]:
            for j in range(i + 1, len(jobs)):
                if j in visited:
                    continue
                if _error_similarity(signatures[i], signatures[j]) >= threshold:
                    group.append(j)
                    visited.add(j)
        groups.append([jobs[k] for k in group])

    merged_count = sum(1 for g in groups if len(g) > 1)
    saved_count = sum(len(g) - 1 for g in groups if len(g) > 1)
    if saved_count:
        log(
            f"  Similarity grouping: {merged_count} multi-job group(s), "
            f"{saved_count} duplicate issue(s) suppressed"
        )
    return groups
