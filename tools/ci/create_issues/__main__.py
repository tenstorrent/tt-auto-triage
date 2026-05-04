#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .detect_failures import download_job_logs, find_failing_jobs, group_similar_jobs
from .download_data import download_workflow_data
from .draft_issues import draft_issue_body
from .helpers import gh, log, sanitize_text
from .issue_state import AUTO_TRIAGE_LABEL, append_base_markers, tracked_pairs_from_issues
from .render_summary import load_all_open_issues, render

TARGET_REPO = os.environ.get("TARGET_REPO", "tenstorrent/tt-metal")
ISSUE_REPO = os.environ.get("ISSUE_REPO", "ebanerjeeTT/issue_dump")
CREATE_ISSUES = os.environ.get("CREATE_ISSUES", "false").lower() == "true"
CONSECUTIVE = int(os.environ.get("CONSECUTIVE_FAILURES", "3"))
ISSUE_WRITE_TOKEN = os.environ.get("ISSUE_WRITE_TOKEN", "")
CURSOR_MODEL = os.environ.get("CURSOR_MODEL", "claude-4-sonnet")
SUMMARY_OUTPUT = os.environ.get("SUMMARY_OUTPUT", "")
MAX_ISSUES = int(os.environ.get("MAX_ISSUES", "0"))
WORKFLOW_FILTER = os.environ.get("WORKFLOW_FILTER", "")
LLM_BACKEND = os.environ.get("LLM_BACKEND", "cursor")


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
    failing_jobs = find_failing_jobs(workflow_data, TARGET_REPO, CONSECUTIVE, tracked_pairs)

    if not failing_jobs:
        log("No new deterministic failures found. Done.")
        print(json.dumps({"created": 0, "skipped": len(tracked_pairs), "failures": []}))
        return 0

    logs_dir = Path("build_ci/create_issues/logs")
    enriched_jobs = download_job_logs(failing_jobs, TARGET_REPO, logs_dir)

    # Group jobs with similar errors to avoid filing near-duplicate issues
    job_groups = group_similar_jobs(enriched_jobs)
    log(f"  {len(enriched_jobs)} failing job(s) → {len(job_groups)} issue group(s) after deduplication")

    summary: list[dict[str, Any]] = []
    created_so_far = 0
    for group in job_groups:
        primary_job = group[0]

        if MAX_ISSUES and created_so_far >= MAX_ISSUES:
            for job in group:
                summary.append(_entry(job, "limit_reached"))
            continue

        # Merge log paths from all jobs in the group for the LLM
        all_log_paths = [p for job in group for p in job.get("log_paths", [])]
        merged_job = {**primary_job, "log_paths": all_log_paths}
        if len(group) > 1:
            merged_job["grouped_jobs"] = group[1:]

        log(f"  Drafting issue via {LLM_BACKEND} agent...")
        agent_result = draft_issue_body(merged_job, all_log_paths, CURSOR_MODEL, CONSECUTIVE)
        if agent_result and agent_result.get("deterministic") is False:
            for job in group:
                summary.append(_entry(job, "agent_skipped", reason=agent_result.get("reason", "not deterministic")))
            continue

        if not agent_result or not agent_result.get("issue_body"):
            for job in group:
                summary.append(_entry(job, "agent_skipped", reason="no issue body from agent"))
            continue

        if not CREATE_ISSUES:
            for job in group:
                summary.append(_entry(job, "dry_run"))
            continue

        extra_jobs = [(j["workflow_name"], j["job_name"]) for j in group[1:]]
        issue_url, issue_title, issue_body = create_issue(
            primary_job, agent_result, extra_jobs=extra_jobs or None
        )
        created_so_far += 1
        open_issues.append({
            "number": issue_url.rsplit("/", 1)[-1],
            "title": issue_title,
            "body": issue_body,
            "url": issue_url,
        })
        # One summary entry per group (one issue filed); secondary jobs are covered
        summary.append(_entry(primary_job, "created", issue=issue_url))

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
