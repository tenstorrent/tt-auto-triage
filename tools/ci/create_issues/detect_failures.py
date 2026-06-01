from __future__ import annotations

import calendar
import difflib as _difflib
import os
import re
import time
from pathlib import Path
from typing import Any

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


def find_failing_jobs(
    workflow_data: list[list[Any]],
    target_repo: str,
    consecutive_high_volume: int = 4,
    consecutive_low_volume: int = 2,
    high_volume_runs_per_day: int = 5,
    tracked_pairs: set[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
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
    results: list[dict[str, Any]] = []
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
            continue
        if not all(run.get("conclusion") == "failure" for run in sorted_runs):
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
            continue

        run_ids = list(run_failed_jobs.keys())
        common_jobs = set(run_failed_jobs[run_ids[0]])
        for run_id in run_ids[1:]:
            common_jobs &= set(run_failed_jobs[run_id])

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
            results.append(
                {
                    "workflow_name": workflow_name,
                    "job_name": job_name,
                    "consecutive": consecutive,
                    "job_urls": [run_failed_jobs[run_id].get(job_name, "") for run_id in run_ids],
                    "run_urls": [run.get("html_url", "") for run in sorted_runs],
                    **boundary_info,
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
# Error signature extraction and similarity
# ---------------------------------------------------------------------------

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_TIMESTAMP_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z\s*", re.MULTILINE)

_ERROR_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.MULTILINE)
    for p in (
        r"TT_FATAL\b[^\n]{0,300}",
        r"TT_THROW:[^\n]{0,300}",
        r"SIGABRT[^\n]{0,200}",
        r"Segmentation fault[^\n]{0,200}",
        r"AssertionError:[^\n]{0,200}",
        r"RuntimeError:[^\n]{0,200}",
        r"FAILED\s+\S+::[^\n]{0,200}",
        # Exclude GTest summary lines like "FAILED ] 4 tests, listed below:" or "FAILED ] N tests"
        r"(?:^|\s)FAILED\s+(?!\])[^\n]{5,200}",
        r"(?:TypeError|ValueError|KeyError|AttributeError|ImportError|OSError|IOError):[^\n]{0,200}",
        r"(?:performance|regression|exceeded|threshold|inference.?time)[^\n]{0,200}",
        r"(?<![a-zA-Z])Error:[^\n]{0,200}",
    )
)


def _normalize_error_signature(raw: str) -> str:
    sig = raw
    sig = re.sub(r"\S+\.(cpp|h|py|cc|cxx|hpp|c):\d+", "", sig)
    sig = re.sub(r" @ \S+", "", sig)
    sig = re.sub(r" in \w+:", "", sig)
    sig = re.sub(r"\S+::\S+\[[^\]]*\]", "", sig)
    sig = re.sub(r"\s+", " ", sig).strip()
    return sig


def _regex_extract_error(log_text: str) -> str:
    """Fallback: extract error signature using regex patterns.

    Uses the latest (terminal) match in the log, not the first hit.
    When multiple patterns match at the same position, earlier patterns win.
    """
    clean = _ANSI_ESCAPE.sub("", log_text)
    clean = _TIMESTAMP_PREFIX.sub("", clean)

    best: re.Match[str] | None = None
    best_priority = len(_ERROR_PATTERNS)

    for priority, pat in enumerate(_ERROR_PATTERNS):
        last_m: re.Match[str] | None = None
        for m in pat.finditer(clean):
            last_m = m
        if last_m is None:
            continue
        if best is None or last_m.start() > best.start() or (
            last_m.start() == best.start() and priority < best_priority
        ):
            best = last_m
            best_priority = priority

    if best is None:
        return ""
    return _normalize_error_signature(best.group(0))


def _error_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return _difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()



# ---------------------------------------------------------------------------
# Consistency gate: drop jobs where error signature differs across runs
# ---------------------------------------------------------------------------

def filter_consistent_failures(
    jobs: list[dict[str, Any]],
    threshold: float = 0.85,
) -> list[dict[str, Any]]:
    """Keep only jobs whose root-cause error is consistent across all consecutive runs.

    Uses regex extraction on each run's log file.  If the extracted signatures
    diverge (similarity < threshold) between any two runs, the job is dropped —
    it is likely a flaky infra issue or an unrelated transient failure rather
    than a deterministic bug.

    A job with no extractable signature in ANY run is also dropped (can't
    confirm the error is real or reproducible).

    Returns the filtered job list.  Each kept job has an ``error_signature``
    key added (the reference signature from run 1) so downstream steps can
    reuse it without re-extracting.
    """
    kept: list[dict[str, Any]] = []

    for job in jobs:
        log_paths = job.get("log_paths", [])
        if not log_paths:
            log(f"  Dropping '{job['job_name']}': no log paths available")
            continue

        sigs: list[str] = []
        for log_path in log_paths:
            try:
                text = Path(log_path).read_text(errors="replace")
                sig = _regex_extract_error(text[-100_000:])
                sigs.append(sig)
            except Exception as exc:
                log(f"  Warning: could not read log {log_path}: {exc}")
                sigs.append("")

        valid_sigs = [s for s in sigs if s]
        if not valid_sigs:
            log(
                f"  Dropping '{job['job_name']}': no error signature found in any of "
                f"{len(log_paths)} run log(s)"
            )
            continue

        # All runs must agree — compare every signature against the first valid one
        reference = valid_sigs[0]
        inconsistent_run: int | None = None
        for idx, sig in enumerate(sigs):
            if not sig:
                # A run with no extractable error is suspicious
                inconsistent_run = idx
                break
            similarity = _error_similarity(reference, sig)
            if similarity < threshold:
                inconsistent_run = idx
                log(
                    f"  Dropping '{job['job_name']}': run {idx + 1} error diverges "
                    f"(similarity {similarity:.2f} < {threshold:.2f})\n"
                    f"    ref:  {reference[:120]}\n"
                    f"    run{idx + 1}: {sig[:120]}"
                )
                break

        if inconsistent_run is not None:
            continue

        kept.append({**job, "error_signature": reference})
        short = reference[:80]
        log(f"  Consistent ({len(sigs)} runs): '{job['job_name']}' — {short}")

    dropped = len(jobs) - len(kept)
    if dropped:
        log(f"  Consistency gate: dropped {dropped}/{len(jobs)} job(s) with inconsistent errors")
    else:
        log(f"  Consistency gate: all {len(jobs)} job(s) passed")
    return kept
