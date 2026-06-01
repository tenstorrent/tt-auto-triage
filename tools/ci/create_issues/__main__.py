#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from .detect_failures import (
    _error_similarity,
    download_job_logs,
    filter_consistent_failures,
    iter_failing_jobs,
)
from .download_data import download_workflow_data
from .draft_issues import draft_issue_body
from .helpers import gh, log, sanitize_text
from .issue_state import AUTO_TRIAGE_LABEL, append_base_markers, tracked_pairs_from_issues
from .render_summary import load_all_open_issues, render

TARGET_REPO = os.environ.get("TARGET_REPO", "tenstorrent/tt-metal")
ISSUE_REPO = os.environ.get("ISSUE_REPO", "tenstorrent/tt-metal")
CREATE_ISSUES = os.environ.get("CREATE_ISSUES", "false").lower() == "true"
# Adaptive consecutive-failure threshold: workflows with more than
# HIGH_VOLUME_RUNS_PER_DAY runs on main in the last 24h require
# CONSECUTIVE_HIGH_VOLUME consecutive failures; less-frequent workflows
# only need CONSECUTIVE_LOW_VOLUME.
CONSECUTIVE_HIGH_VOLUME = int(os.environ.get("CONSECUTIVE_FAILURES_HIGH_VOLUME", "4"))
CONSECUTIVE_LOW_VOLUME = int(os.environ.get("CONSECUTIVE_FAILURES_LOW_VOLUME", "2"))
HIGH_VOLUME_RUNS_PER_DAY = int(os.environ.get("HIGH_VOLUME_RUNS_PER_DAY", "5"))
ISSUE_WRITE_TOKEN = os.environ.get("ISSUE_WRITE_TOKEN", "")
SUMMARY_OUTPUT = os.environ.get("SUMMARY_OUTPUT", "")
MAX_ISSUES = int(os.environ.get("MAX_ISSUES", "0"))
WORKFLOW_FILTER = os.environ.get("WORKFLOW_FILTER", "")

DEDUP_THRESHOLD = 0.85
AGENT_EXCERPT_MATCH_THRESHOLD = 0.60


def _entry(job: dict[str, Any], action: str, **kwargs: Any) -> dict[str, Any]:
    return {"workflow_name": job["workflow_name"], "job": job["job_name"], "action": action, **kwargs}


def _log_rejection(job: dict[str, Any], stage: str, reason: str) -> None:
    log(f"  Rejected ({stage}): {job['workflow_name']} / {job['job_name']} — {reason}")


def _normalize_for_compare(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _has_clear_regression_boundary(job: dict[str, Any]) -> bool:
    last_passing_raw = job.get("last_passing_sha")
    first_failing_raw = job.get("first_failing_sha")
    if not last_passing_raw or not first_failing_raw:
        return False
    last_passing_sha = str(last_passing_raw).strip()
    first_failing_sha = str(first_failing_raw).strip()
    if not last_passing_sha or not first_failing_sha:
        return False
    return last_passing_sha != first_failing_sha


def _agent_excerpt_matches_signature(signature: str, error_excerpt: str) -> bool:
    sig = _normalize_for_compare(signature)
    excerpt = _normalize_for_compare(error_excerpt)
    if not sig:
        return True
    if not excerpt:
        return False
    if sig in excerpt or excerpt in sig:
        return True
    if _error_similarity(sig, excerpt) >= AGENT_EXCERPT_MATCH_THRESHOLD:
        return True
    for line in error_excerpt.splitlines():
        normalized_line = _normalize_for_compare(line)
        if not normalized_line:
            continue
        if _error_similarity(sig, normalized_line) >= AGENT_EXCERPT_MATCH_THRESHOLD:
            return True
    return False


def create_issue(
    job: dict[str, Any],
    agent_result: dict[str, Any],
) -> tuple[str, str, str]:
    title = sanitize_text(
        agent_result.get("issue_title", f"[CI] {job['workflow_name']} / {job['job_name']}")
    )
    body = append_base_markers(
        sanitize_text(agent_result["issue_body"]),
        workflow_name=job["workflow_name"],
        job_name=job["job_name"],
    )
    issue_url = gh(
        "issue", "create",
        f"--repo={ISSUE_REPO}",
        f"--title={title}",
        f"--body={body}",
        f"--label={AUTO_TRIAGE_LABEL}",
        token=ISSUE_WRITE_TOKEN,
    ).strip()
    log(f"  Created issue: {issue_url}")
    return issue_url, title, body


def main() -> int:
    log("=== Create Issues ===")
    if not os.environ.get("COPILOT_PAT"):
        log("COPILOT_PAT is required.")
        return 1
    if CONSECUTIVE_HIGH_VOLUME < CONSECUTIVE_LOW_VOLUME:
        log(
            f"CONSECUTIVE_FAILURES_HIGH_VOLUME ({CONSECUTIVE_HIGH_VOLUME}) must be "
            f">= CONSECUTIVE_FAILURES_LOW_VOLUME ({CONSECUTIVE_LOW_VOLUME})"
        )
        return 1
    workflow_data = download_workflow_data(TARGET_REPO)
    filters = [f.strip().lower() for f in WORKFLOW_FILTER.split(",") if f.strip()]
    if filters:
        orig_count = len(workflow_data)
        workflow_data = [(name, runs) for name, runs in workflow_data
                         if any(f in str(name).lower() for f in filters)]
        log(f"  Workflow filter {filters}: {len(workflow_data)}/{orig_count} workflows matched")
    open_issues = load_all_open_issues(ISSUE_REPO, ISSUE_WRITE_TOKEN)
    tracked_pairs = tracked_pairs_from_issues(open_issues)
    logs_dir = Path("build_ci/create_issues/logs")

    summary: list[dict[str, Any]] = []
    created_so_far = 0
    processed_signatures: list[str] = []
    processed_candidates = 0

    for job in iter_failing_jobs(
        workflow_data,
        TARGET_REPO,
        consecutive_high_volume=CONSECUTIVE_HIGH_VOLUME,
        consecutive_low_volume=CONSECUTIVE_LOW_VOLUME,
        high_volume_runs_per_day=HIGH_VOLUME_RUNS_PER_DAY,
        tracked_pairs=tracked_pairs,
    ):
        processed_candidates += 1
        log(
            f"Processing candidate {processed_candidates}: "
            f"{job['workflow_name']} / {job['job_name']}"
        )

        # ── Early exit: MAX_ISSUES reached ──────────────────────────
        if MAX_ISSUES and created_so_far >= MAX_ISSUES:
            reason = "max issues reached; remaining candidates not evaluated"
            _log_rejection(job, "global-limit", reason)
            summary.append(_entry(job, "limit_reached", reason=reason))
            break

        # ── Step 1: Download logs for THIS job only ─────────────────
        enriched = download_job_logs([job], TARGET_REPO, logs_dir)
        if not enriched:
            reason = "log download returned nothing"
            _log_rejection(job, "pre-agent/log-download", reason)
            summary.append(_entry(job, "inconsistent_error", reason=reason))
            continue
        enriched_job = enriched[0]

        # ── Step 2: Consistency gate ────────────────────────────────
        # NOTE: filter_consistent_failures receives a single-element list
        # (one job), but that job carries log_paths from ALL N consecutive
        # failing runs (downloaded in Step 1).  The gate compares error
        # signatures across those per-run logs — it is NOT single-sample.
        # TODO: If we ever want cross-job consistency (comparing different
        # jobs in the same workflow), we'd need to restructure the outer
        # loop to batch jobs before calling the gate.
        consistent = filter_consistent_failures([enriched_job])
        if not consistent:
            reason = "consistency gate failed"
            _log_rejection(job, "pre-agent/consistency-gate", reason)
            summary.append(_entry(job, "inconsistent_error", reason=reason))
            continue
        consistent_job = consistent[0]

        # ── Step 3: Cross-job dedup against already-processed sigs ──
        sig = consistent_job.get("error_signature", "")
        is_duplicate = False
        if sig:
            for prev_sig in processed_signatures:
                if _error_similarity(sig, prev_sig) >= DEDUP_THRESHOLD:
                    reason = "error signature similar to an already-processed candidate"
                    _log_rejection(job, "pre-agent/dedup", reason)
                    summary.append(_entry(job, "duplicate_suppressed", reason=reason))
                    is_duplicate = True
                    break
        if is_duplicate:
            continue

        # ── Step 4: Draft issue body via LLM ────────────────────────
        log_paths = consistent_job.get("log_paths", [])
        log("  Drafting issue via Copilot agent...")
        per_workflow_consecutive = consistent_job.get("consecutive", CONSECUTIVE_LOW_VOLUME)
        agent_result = draft_issue_body(consistent_job, log_paths, consecutive=per_workflow_consecutive)

        if agent_result and agent_result.get("deterministic") is False:
            reason = str(agent_result.get("reason", "not deterministic"))
            _log_rejection(job, "agent", reason)
            summary.append(_entry(job, "agent_skipped", reason=reason))
            continue

        if not agent_result:
            reason = "no result from agent"
            _log_rejection(job, "agent", reason)
            summary.append(_entry(job, "agent_skipped", reason=reason))
            continue

        if not agent_result.get("issue_body"):
            reason = "no issue body from agent"
            _log_rejection(job, "agent", reason)
            summary.append(_entry(job, "agent_skipped", reason=reason))
            continue

        if not _has_clear_regression_boundary(consistent_job):
            reason = "unclear regression boundary"
            _log_rejection(job, "post-agent/gates", reason)
            summary.append(_entry(job, "agent_skipped", reason=reason))
            continue

        confidence = str(agent_result.get("confidence", "")).strip().lower()
        if confidence not in {"medium", "high"}:
            reason = f"low confidence: {confidence or 'unknown'}"
            _log_rejection(job, "post-agent/gates", reason)
            summary.append(_entry(job, "agent_skipped", reason=reason))
            continue

        error_excerpt = agent_result.get("error_excerpt") or ""
        if not _agent_excerpt_matches_signature(sig, str(error_excerpt)):
            reason = "error excerpt mismatch vs extracted signature"
            _log_rejection(job, "post-agent/gates", reason)
            summary.append(_entry(job, "agent_skipped", reason=reason))
            continue

        # ── Step 5: Dry-run gate ────────────────────────────────────
        if not CREATE_ISSUES:
            log(f"  Eligible but CREATE_ISSUES=false (dry run): {job['workflow_name']} / {job['job_name']}")
            summary.append(_entry(job, "dry_run"))
            # Still record the signature so future iterations dedup against it
            if sig:
                processed_signatures.append(sig)
            continue

        # ── Step 6: Create the issue ────────────────────────────────
        issue_url, issue_title, issue_body = create_issue(
            consistent_job, agent_result,
        )
        created_so_far += 1
        if sig:
            processed_signatures.append(sig)
        open_issues.append({
            "number": issue_url.rsplit("/", 1)[-1],
            "title": issue_title,
            "body": issue_body,
            "url": issue_url,
        })
        summary.append(_entry(job, "created", issue=issue_url))

    if processed_candidates == 0:
        log("No new deterministic failures found. Done.")
        print(json.dumps({"created": 0, "skipped": len(tracked_pairs), "failures": []}))
        return 0
    log(f"Processed {processed_candidates} candidate job(s)")

    markdown = render(summary, open_issues)
    if SUMMARY_OUTPUT:
        Path(SUMMARY_OUTPUT).write_text(markdown, encoding="utf-8")
    else:
        print(markdown)

    print(
        json.dumps({"created": created_so_far, "skipped": len(tracked_pairs), "failures": summary}, indent=2),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
