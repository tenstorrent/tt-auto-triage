#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .detect_failures import (
    download_job_logs,
    iter_failing_jobs,
    resolve_last_passing_boundary,
    streak_still_failing,
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

def _entry(job: dict[str, Any], action: str, **kwargs: Any) -> dict[str, Any]:
    return {"workflow_name": job["workflow_name"], "job": job["job_name"], "action": action, **kwargs}


def _write_issues_json(open_issues: list[dict[str, Any]], pre_existing_count: int) -> None:
    existing = open_issues[:pre_existing_count]
    new = open_issues[pre_existing_count:]
    artifact = {
        "new_issues": [{"url": i.get("url", "")} for i in new],
        "existing_issues": [{"url": i.get("url", "")} for i in existing],
    }
    Path("issues.json").write_text(json.dumps(artifact, indent=2), encoding="utf-8")


def _log_rejection(job: dict[str, Any], stage: str, reason: str) -> None:
    log(f"  Rejected ({stage}): {job['workflow_name']} / {job['job_name']} — {reason}")


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
    pre_existing_count = len(open_issues)
    tracked_pairs = tracked_pairs_from_issues(open_issues)
    logs_dir = Path("build_ci/create_issues/logs")

    runs_by_workflow = {name: runs for name, runs in workflow_data}

    summary: list[dict[str, Any]] = []
    created_so_far = 0
    candidates = list(
        iter_failing_jobs(
            workflow_data,
            TARGET_REPO,
            consecutive_high_volume=CONSECUTIVE_HIGH_VOLUME,
            consecutive_low_volume=CONSECUTIVE_LOW_VOLUME,
            high_volume_runs_per_day=HIGH_VOLUME_RUNS_PER_DAY,
            tracked_pairs=tracked_pairs,
        )
    )
    total_candidates = len(candidates)

    for processed_candidates, job in enumerate(candidates, start=1):
        log(
            f"Processing candidate {processed_candidates}/{total_candidates}: "
            f"{job['workflow_name']} / {job['job_name']}"
        )

        # ── Early exit: MAX_ISSUES reached ──────────────────────────
        if MAX_ISSUES and created_so_far >= MAX_ISSUES:
            reason = "max issues reached; remaining candidates not evaluated"
            _log_rejection(job, "global-limit", reason)
            summary.append(_entry(job, "limit_reached", reason=reason))
            break

        # ── Step 1: Live freshness re-check (snapshot may be stale) ──
        # The detector reads a periodic snapshot artifact that can be hours
        # old. Confirm the job still fails in the newest completed run on main
        # before spending the agent/log-download cost on a recovered streak.
        still_failing, freshness_reason = streak_still_failing(job, TARGET_REPO)
        if not still_failing:
            _log_rejection(job, "freshness", freshness_reason)
            summary.append(_entry(job, "stale_recovered", reason=freshness_reason))
            continue

        # ── Step 2: Download logs for THIS job only ─────────────────
        enriched = download_job_logs([job], TARGET_REPO, logs_dir)
        if not enriched:
            reason = "log download returned nothing"
            _log_rejection(job, "pre-agent/log-download", reason)
            summary.append(_entry(job, "agent_skipped", reason=reason))
            continue
        enriched_job = enriched[0]

        # ── Step 3: Resolve the last-passing boundary ───────────────
        # Deferred until here because it costs API calls: only candidates that
        # survived the gates above will actually reach the issue body.
        last_passing = resolve_last_passing_boundary(
            enriched_job["job_name"],
            runs_by_workflow.get(enriched_job["workflow_name"], []),
            TARGET_REPO,
        )
        if last_passing:
            enriched_job["last_passing_sha"] = last_passing["head_sha"]
            enriched_job["last_passing_date"] = last_passing["created_at"]
            enriched_job["last_passing_url"] = last_passing["job_url"]
            log(f"  Last passing: {last_passing['head_sha'][:12]} ({last_passing['created_at']})")
        else:
            log("  Last passing: not found within search budget")

        # ── Step 4: Draft issue body via LLM ────────────────────────
        log_paths = enriched_job.get("log_paths", [])
        log("  Drafting issue via Copilot agent...")
        per_workflow_consecutive = enriched_job.get("consecutive", CONSECUTIVE_LOW_VOLUME)
        agent_result = draft_issue_body(enriched_job, log_paths, consecutive=per_workflow_consecutive)

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

        confidence = str(agent_result.get("confidence", "")).strip().lower()
        if confidence not in {"medium", "high"}:
            reason = f"low confidence: {confidence or 'unknown'}"
            _log_rejection(job, "post-agent/gates", reason)
            summary.append(_entry(job, "agent_skipped", reason=reason))
            continue

        # ── Step 5: Dry-run gate ────────────────────────────────────
        if not CREATE_ISSUES:
            log(f"  Eligible but CREATE_ISSUES=false (dry run): {job['workflow_name']} / {job['job_name']}")
            summary.append(_entry(job, "dry_run"))
            continue

        # ── Step 6: Create the issue ────────────────────────────────
        issue_url, issue_title, issue_body = create_issue(
            enriched_job, agent_result,
        )
        created_so_far += 1
        open_issues.append({
            "number": issue_url.rsplit("/", 1)[-1],
            "title": issue_title,
            "body": issue_body,
            "url": issue_url,
        })
        summary.append(_entry(job, "created", issue=issue_url))

    if total_candidates == 0:
        log("No new deterministic failures found. Done.")
        _write_issues_json(open_issues, pre_existing_count)
        print(json.dumps({
            "created": 0,
            "skipped": 0,
            "tracked_pairs_open": len(tracked_pairs),
            "failures": [],
        }))
        return 0
    log(f"Processed {processed_candidates}/{total_candidates} candidate job(s)")

    markdown = render(summary, open_issues)
    if SUMMARY_OUTPUT:
        Path(SUMMARY_OUTPUT).write_text(markdown, encoding="utf-8")
    else:
        print(markdown)

    _write_issues_json(open_issues, pre_existing_count)

    skipped_count = sum(1 for item in summary if item.get("action") != "created")
    print(
        json.dumps(
            {
                "created": created_so_far,
                "skipped": skipped_count,
                "tracked_pairs_open": len(tracked_pairs),
                "failures": summary,
            },
            indent=2,
        ),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
