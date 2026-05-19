from __future__ import annotations

import calendar
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
            except RuntimeError as exc:
                log(f"  Warning: failed to fetch jobs for run {run_id}: {exc} — skipping run")
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
# Deduplication: group jobs with similar errors
# ---------------------------------------------------------------------------
import difflib as _difflib

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
        r"Error:[^\n]{0,200}",
    )
)

_EXTRACT_ERROR_PROMPT = """\
You are analyzing a CI job failure log. Your task is to extract the specific root-cause error.

Rules:
- Return ONLY the error message text — no explanation, no context, no formatting.
- Ignore infrastructure noise: package installation failures, network timeouts, hugepages errors, \
OOM killer messages, disk full, device unavailable, environment setup failures.
- Focus on the actual test/assertion failure (e.g. Python exception, TT_FATAL, SIGABRT, \
pytest FAILED, assertion error, shape mismatch, wrong output value).
- If multiple errors exist, return only the primary root cause.
- If no meaningful test failure is found (only infrastructure errors), return exactly: NO_TEST_FAILURE

Log tail (last ~5000 characters):
{log_tail}
"""


def _extract_error_with_llm(
    log_text: str,
    model: str,
    backend: str,
) -> str:
    """Call the LLM to extract the specific root-cause error from a log.

    Returns the extracted error string, or empty string on failure.
    Falls back to empty string so the caller can use regex extraction instead.
    """
    # Lazy import to avoid circular dependency at module load time
    from .draft_issues import _run_llm_agent  # noqa: PLC0415

    tail = log_text[-5_000:]
    prompt = _EXTRACT_ERROR_PROMPT.format(log_tail=tail)
    try:
        result = _run_llm_agent(prompt, model=model, backend=backend)
        # Strip markdown fences if the agent wrapped the result
        result = result.strip()
        if result.startswith("```"):
            result = result.split("\n", 1)[-1].rstrip("`").strip()
        if result == "NO_TEST_FAILURE" or not result:
            return ""
        return result[:500]  # cap length to prevent pathological inputs
    except Exception as exc:
        log(f"    LLM error extraction failed ({type(exc).__name__}): {exc}")
        return ""


def _regex_extract_error(log_text: str) -> str:
    """Fallback: extract error signature using regex patterns."""
    clean = _ANSI_ESCAPE.sub("", log_text)
    clean = _TIMESTAMP_PREFIX.sub("", clean)
    for pat in _ERROR_PATTERNS:
        m = pat.search(clean)
        if m:
            sig = m.group(0)
            sig = re.sub(r"\S+\.(cpp|h|py|cc|cxx|hpp|c):\d+", "", sig)
            sig = re.sub(r" @ \S+", "", sig)
            sig = re.sub(r" in \w+:", "", sig)
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
    threshold: float = 0.90,
    model: str = "claude-4-sonnet",
    backend: str = "cursor",
) -> list[list[dict[str, Any]]]:
    """Group jobs with similar error signatures to avoid filing duplicate issues.

    Extracts the root-cause error via LLM for accurate comparison, falling back
    to regex extraction if the LLM call fails.  Uses a high similarity threshold
    (default 0.90) to avoid false-positive grouping of unrelated failures.

    Returns a list of groups; each group is a non-empty list of job dicts.
    The first entry in each group is the *primary* job (drives issue creation).
    """
    signatures: list[str] = []
    sig_source: list[str] = []  # "llm" or "regex" per job, for logging

    for job in jobs:
        log_text = ""
        for log_path in job.get("log_paths", []):
            try:
                text = Path(log_path).read_text(errors="replace")
                # Prefer the most recent run (first log path) and read from tail
                log_text = text[-100_000:]
                break
            except Exception:
                pass

        sig = ""
        source = "none"
        if log_text:
            sig = _extract_error_with_llm(log_text, model=model, backend=backend)
            if sig:
                source = "llm"
            else:
                # LLM failed or returned no test failure — fall back to regex
                sig = _regex_extract_error(log_text)
                if sig:
                    source = "regex"

        signatures.append(sig)
        sig_source.append(source)
        short = sig[:80] if sig else "(no signature)"
        log(f"    Sig [{source}] [{job['job_name']!r:.40}]: {short}")

    visited: set[int] = set()
    groups: list[list[dict[str, Any]]] = []
    for i in range(len(jobs)):
        if i in visited:
            continue
        group = [i]
        visited.add(i)
        # Only group if both jobs have signatures from the same source class
        # (don't mix LLM-extracted with regex-extracted — quality mismatch)
        if signatures[i] and sig_source[i] == "llm":
            effective_threshold = threshold  # high threshold for LLM extractions
        elif signatures[i]:
            effective_threshold = 0.65  # original threshold for regex fallback
        else:
            effective_threshold = 1.1  # impossible threshold → no grouping

        for j in range(i + 1, len(jobs)):
            if j in visited:
                continue
            # Only group LLM-with-LLM or regex-with-regex, not mixed
            if sig_source[j] != sig_source[i]:
                continue
            if _error_similarity(signatures[i], signatures[j]) >= effective_threshold:
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
