#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .detect_failures import (
    _error_similarity,
    download_job_logs,
    filter_consistent_failures,
    find_failing_jobs,
)
from .download_data import download_workflow_data
from .draft_issues import draft_issue_body
from .helpers import gh, log, sanitize_text
from .issue_state import AUTO_TRIAGE_LABEL, append_base_markers, tracked_pairs_from_issues
from .render_summary import load_all_open_issues, render

TARGET_REPO = os.environ.get("TARGET_REPO", "tenstorrent/tt-metal")
ISSUE_REPO = os.environ.get("ISSUE_REPO", "ebanerjeeTT/issue_dump")
CREATE_ISSUES = os.environ.get("CREATE_ISSUES", "false").lower() == "true"
# Adaptive consecutive-failure threshold: workflows with more than
# HIGH_VOLUME_RUNS_PER_DAY runs on main in the last 24h require
# CONSECUTIVE_HIGH_VOLUME consecutive failures; less-frequent workflows
# only need CONSECUTIVE_LOW_VOLUME.
CONSECUTIVE_HIGH_VOLUME = int(os.environ.get("CONSECUTIVE_FAILURES_HIGH_VOLUME", "4"))
CONSECUTIVE_LOW_VOLUME = int(os.environ.get("CONSECUTIVE_FAILURES_LOW_VOLUME", "2"))
HIGH_VOLUME_RUNS_PER_DAY = int(os.environ.get("HIGH_VOLUME_RUNS_PER_DAY", "5"))
ISSUE_WRITE_TOKEN = os.environ.get("ISSUE_WRITE_TOKEN", "")
CURSOR_MODEL = os.environ.get("CURSOR_MODEL", "claude-4-sonnet")
SUMMARY_OUTPUT = os.environ.get("SUMMARY_OUTPUT", "")
MAX_ISSUES = int(os.environ.get("MAX_ISSUES", "0"))
WORKFLOW_FILTER = os.environ.get("WORKFLOW_FILTER", "")
LLM_BACKEND = os.environ.get("LLM_BACKEND", "cursor")

DEDUP_THRESHOLD = 0.85


def _entry(job: dict[str, Any], action: str, **kwargs: Any) -> dict[str, Any]:
    return {"workflow_name": job["workflow_name"], "job": job["job_name"], "action": action, **kwargs}


def create_issue(
    job: dict[str, Any],
    agent_result: dict[str, Any],
    extra_jobs: list[tuple[str, str]] | None = None,
) -> tuple[str, str, str]:
    title = sanitize_text(
        agent_result.get("issue_title", f"[CI] {job['workflow_name']} / {job['job_name']}")
    )
    body = append_base_markers(
        sanitize_text(agent_result["issue_body"]),
        workflow_name=job["workflow_name"],
        job_name=job["job_name"],
        extra_jobs=extra_jobs,
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
    if LLM_BACKEND not in ("cursor", "copilot"):
        log(f"LLM_BACKEND must be 'cursor' or 'copilot', got: {LLM_BACKEND!r}")
        return 1
    if LLM_BACKEND == "copilot" and not os.environ.get("COPILOT_GITHUB_TOKEN"):
        log("COPILOT_GITHUB_TOKEN is required when LLM_BACKEND=copilot.")
        return 1
    if LLM_BACKEND == "cursor" and not os.environ.get("CURSOR_API_KEY"):
        log("CURSOR_API_KEY is required when LLM_BACKEND=cursor.")
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
    failing_jobs = find_failing_jobs(
        workflow_data,
        TARGET_REPO,
        consecutive_high_volume=CONSECUTIVE_HIGH_VOLUME,
        consecutive_low_volume=CONSECUTIVE_LOW_VOLUME,
        high_volume_runs_per_day=HIGH_VOLUME_RUNS_PER_DAY,
        tracked_pairs=tracked_pairs,
    )

    if not failing_jobs:
        log("No new deterministic failures found. Done.")
        print(json.dumps({"created": 0, "skipped": len(tracked_pairs), "failures": []}))
        return 0

    logs_dir = Path("build_ci/create_issues/logs")

    summary: list[dict[str, Any]] = []
    created_so_far = 0
    processed_signatures: list[str] = []

    for i, job in enumerate(failing_jobs):
        log(f"Processing job {i+1}/{len(failing_jobs)}: {job['workflow_name']} / {job['job_name']}")

        # ── Early exit: MAX_ISSUES reached ──────────────────────────
        if MAX_ISSUES and created_so_far >= MAX_ISSUES:
            # Mark this job AND all remaining jobs as limit_reached
            for remaining_job in failing_jobs[i:]:
                summary.append(_entry(remaining_job, "limit_reached"))
            break

        # ── Step 1: Download logs for THIS job only ─────────────────
        enriched = download_job_logs([job], TARGET_REPO, logs_dir)
        if not enriched:
            summary.append(_entry(job, "inconsistent_error", reason="log download returned nothing"))
            continue
        enriched_job = enriched[0]

        # ── Step 2: Consistency gate ────────────────────────────────
        consistent = filter_consistent_failures([enriched_job])
        if not consistent:
            summary.append(_entry(job, "inconsistent_error"))
            continue
        consistent_job = consistent[0]

        # ── Step 3: Cross-job dedup against already-processed sigs ──
        sig = consistent_job.get("error_signature", "")
        is_duplicate = False
        if sig:
            for prev_sig in processed_signatures:
                if _error_similarity(sig, prev_sig) >= DEDUP_THRESHOLD:
                    log(f"  Duplicate suppressed: error similar to an already-filed job")
                    summary.append(_entry(job, "duplicate_suppressed"))
                    is_duplicate = True
                    break
        if is_duplicate:
            continue

        # ── Step 4: Draft issue body via LLM ────────────────────────
        log_paths = consistent_job.get("log_paths", [])
        log(f"  Drafting issue via {LLM_BACKEND} agent...")
        per_workflow_consecutive = consistent_job.get("consecutive", CONSECUTIVE_LOW_VOLUME)
        agent_result = draft_issue_body(consistent_job, log_paths, CURSOR_MODEL, per_workflow_consecutive)

        if agent_result and agent_result.get("deterministic") is False:
            summary.append(_entry(job, "agent_skipped", reason=agent_result.get("reason", "not deterministic")))
            continue

        if not agent_result or not agent_result.get("issue_body"):
            summary.append(_entry(job, "agent_skipped", reason="no issue body from agent"))
            continue

        # ── Step 5: Dry-run gate ────────────────────────────────────
        if not CREATE_ISSUES:
            summary.append(_entry(job, "dry_run"))
            # Still record the signature so future iterations dedup against it
            if sig:
                processed_signatures.append(sig)
            continue

        # ── Step 6: Create the issue ────────────────────────────────
        issue_url, issue_title, issue_body = create_issue(
            consistent_job, agent_result, extra_jobs=None,
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
