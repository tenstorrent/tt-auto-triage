#!/usr/bin/env python3
"""Orchestrator: detect deterministic CI failures, draft issues via Cursor agent, create them, notify via Slack.

All configuration is via environment variables:
  TARGET_REPO          -- repo to analyze (default: tenstorrent/tt-metal)
  ISSUE_REPO           -- repo to create issues in (default: ebanerjeeTT/issue_dump)
  CREATE_ISSUES        -- "true" to actually create issues (default: "false")
  CONSECUTIVE_FAILURES -- how many consecutive failures required (default: 3)
  SLACK_BOT_TOKEN      -- Slack bot token for notifications
  SLACK_CHANNEL_ID     -- Slack channel to post to
  OWNERS_JSON_PATH     -- path to owners.json
  PIPELINE_REORG_DIR   -- path to pipeline_reorg YAML directory
  ISSUE_WRITE_TOKEN    -- GitHub token with issue write access
  GITHUB_TOKEN         -- GitHub token for reading workflow data
  CURSOR_API_KEY       -- Cursor API key (required for agent drafting)
  CURSOR_MODEL         -- Cursor model to use (default: claude-4-sonnet)
  SUMMARY_OUTPUT       -- path to write markdown summary (default: stdout)
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from tools.ci.helpers import (
    download_slack_directory,
    fingerprint_for,
    gh,
    log,
    slack_post,
)
from tools.ci.download_data import download_workflow_data
from tools.ci.detect_failures import find_failing_jobs, download_job_logs
from tools.ci.resolve_owners import (
    load_codeowners,
    load_owners_json,
    load_pipeline_reorg_owners,
    resolve_owners,
)
from tools.ci.draft_issues import draft_issue_body
from tools.ci.render_summary import load_all_open_issues, render


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TARGET_REPO = os.environ.get("TARGET_REPO", "tenstorrent/tt-metal")
ISSUE_REPO = os.environ.get("ISSUE_REPO", "ebanerjeeTT/issue_dump")
CREATE_ISSUES = os.environ.get("CREATE_ISSUES", "false").lower() == "true"
CONSECUTIVE = int(os.environ.get("CONSECUTIVE_FAILURES", "3"))
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "")
OWNERS_JSON_PATH = Path(os.environ.get("OWNERS_JSON_PATH", "tt-metal/.github/actions/analyze-workflow-data/owners.json"))
PIPELINE_REORG_DIR = Path(os.environ.get("PIPELINE_REORG_DIR", "tt-metal/tests/pipeline_reorg"))
ISSUE_WRITE_TOKEN = os.environ.get("ISSUE_WRITE_TOKEN", "")
CURSOR_MODEL = os.environ.get("CURSOR_MODEL", "claude-4-sonnet")
SUMMARY_OUTPUT = os.environ.get("SUMMARY_OUTPUT", "")
MAX_ISSUES = int(os.environ.get("MAX_ISSUES", "0"))  # 0 = unlimited
CODEOWNERS_PATH = Path(os.environ.get("CODEOWNERS_PATH", "tt-metal/.github/CODEOWNERS"))


# ---------------------------------------------------------------------------
# Issue check / create
# ---------------------------------------------------------------------------


def create_issue(job: dict[str, Any], agent_result: dict[str, Any]) -> tuple[str, str, str]:
    """Create a GitHub issue. Returns (url, title, final_body)."""
    title = agent_result.get("issue_title", f"[CI] {job['workflow_name']} / {job['job_name']}")
    body = agent_result["issue_body"]
    signature = agent_result.get("signature", "")
    if signature:
        fp = fingerprint_for(job["workflow_name"], job["job_name"], signature)
        if "Auto-triage-fingerprint:" not in body:
            body += f"\n\n`Auto-triage-fingerprint: {fp}`"
    # Name markers are used by future runs to skip already-tracked jobs.
    if "Auto-triage-workflow:" not in body:
        body += f"\n`Auto-triage-workflow: {job['workflow_name']}`"
    if "Auto-triage-job-name:" not in body:
        body += f"\n`Auto-triage-job-name: {job['job_name']}`"
    workflow_file = job.get("workflow_file", "")
    if workflow_file and "Auto-triage-workflow-file:" not in body:
        body += f"\n`Auto-triage-workflow-file: {workflow_file}`"

    raw = gh(
        "issue", "create",
        f"--repo={ISSUE_REPO}",
        f"--title={title}",
        f"--body={body}",
        "--label=CI auto triage",  # label used by load_all_open_issues to scope dedup queries
        token=ISSUE_WRITE_TOKEN,
    )
    issue_url = raw.strip()
    log(f"  Created issue: {issue_url}")
    return issue_url, title, body


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------


def send_slack_notification(job: dict[str, Any], issue_url: str, owners: list[dict[str, str]]) -> None:
    if not SLACK_BOT_TOKEN or not SLACK_CHANNEL_ID:
        log("  Skipping Slack notification (no token or channel)")
        return
    if owners:
        parts: list[str] = []
        for o in owners:
            oid = o["id"]
            source = o.get("source", "")
            if source in ("CODEOWNERS", "agent"):
                # GitHub username that couldn't be resolved to a Slack ID;
                # rendered as plain text rather than a mention to avoid a
                # broken <@...> in Slack.
                parts.append(f"`{oid}` ({o.get('name') or oid}, via {source})")
            else:
                parts.append(f"<@{oid}>")
        owner_text = ", ".join(parts)
    else:
        owner_text = "No owners identified -- please triage manually"
    run_links = ", ".join(f"<{url}|run>" for url in job["run_urls"][:3] if url)
    text = (
        f":rotating_light: *New CI auto-triage issue created*\n"
        f"*Job:* `{job['workflow_name']} / {job['job_name']}`\n"
        f"*Issue:* {issue_url}\n"
        f"*Failed {CONSECUTIVE} runs in a row:* {run_links}\n"
        f"*Likely owners:* {owner_text}"
    )
    slack_post(SLACK_BOT_TOKEN, SLACK_CHANNEL_ID, text)
    log(f"  Sent Slack notification for {job['job_name']}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    log("=== Auto Issue Creation ===")
    log(f"Target repo: {TARGET_REPO}")
    log(f"Issue repo:  {ISSUE_REPO}")
    log(f"Create issues: {CREATE_ISSUES}")
    log(f"Max issues: {MAX_ISSUES or 'unlimited'}")

    # Step 1: Download workflow data
    workflow_data = download_workflow_data(TARGET_REPO)

    # Step 2: Load existing issues before failure analysis so find_failing_jobs
    # can skip jobs that are already tracked, avoiding unnecessary log downloads
    # and agent calls for known failures.
    open_issues = load_all_open_issues(ISSUE_REPO, ISSUE_WRITE_TOKEN)
    log(f"Loaded {len(open_issues)} open issues from {ISSUE_REPO}")
    tracked_pairs: set[tuple[str, str]] = set()
    for issue in open_issues:
        body = issue.get("body") or ""
        wf_m = re.search(r"Auto-triage-workflow:\s*`?([^`\n]+)`?", body)
        jn_m = re.search(r"Auto-triage-job-name:\s*`?([^`\n]+)`?", body)
        if wf_m and jn_m:
            tracked_pairs.add((wf_m.group(1).strip(), jn_m.group(1).strip()))
    log(f"  Found {len(tracked_pairs)} already-tracked jobs")

    # Step 3: Find failing jobs, skipping any already tracked
    failing_jobs = find_failing_jobs(workflow_data, TARGET_REPO, CONSECUTIVE, tracked_pairs)

    if not failing_jobs:
        log("No new deterministic failures found. Done.")
        print(json.dumps({"created": 0, "skipped": 0, "failures": []}))
        return 0

    # Step 4: Load owners
    owners_json = load_owners_json(OWNERS_JSON_PATH)
    pipeline_owners = load_pipeline_reorg_owners(PIPELINE_REORG_DIR)
    codeowners = load_codeowners(CODEOWNERS_PATH)
    log(f"Loaded {len(owners_json)} owners.json entries, {len(pipeline_owners)} pipeline_reorg entries, {len(codeowners)} CODEOWNERS rules")

    # Step 4b: Download Slack directory for GitHub username -> Slack ID resolution
    slack_directory: list[dict[str, Any]] = []
    if SLACK_BOT_TOKEN:
        try:
            slack_directory = download_slack_directory(SLACK_BOT_TOKEN)
            log(f"Downloaded Slack directory: {len(slack_directory)} users")
        except Exception as exc:
            log(f"  Warning: failed to download Slack directory: {exc}")
    else:
        log("  Skipping Slack directory download (no token)")

    # Step 5: Download logs -- all failing_jobs are new (tracked jobs were
    # filtered out in find_failing_jobs before being returned).
    summary: list[dict[str, Any]] = []
    created_so_far = 0
    if failing_jobs:
        logs_dir = Path("build_ci/auto_issues/logs")
        enriched_jobs = download_job_logs(failing_jobs, TARGET_REPO, logs_dir)

        for job in enriched_jobs:
            if MAX_ISSUES and created_so_far >= MAX_ISSUES:
                log(f"  Hit MAX_ISSUES limit ({MAX_ISSUES}), stopping")
                summary.append({
                    "workflow_name": job["workflow_name"],
                    "job": job["job_name"],
                    "action": "limit_reached",
                })
                continue

            # Step 6: Cursor agent drafting (before owner resolution so we can use suggested_owners)
            agent_result: dict[str, Any] | None = None
            codeowners_abs = str(CODEOWNERS_PATH) if CODEOWNERS_PATH.exists() else ""
            if os.environ.get("CURSOR_API_KEY"):
                log(f"  Drafting issue via Cursor agent...")
                agent_result = draft_issue_body(
                    job, job.get("log_paths", []), CURSOR_MODEL,
                    codeowners_path=codeowners_abs,
                    consecutive=CONSECUTIVE,
                )
                if agent_result:
                    det = agent_result.get("deterministic", False)
                    conf = agent_result.get("confidence", "?")
                    log(f"  Agent: deterministic={det}, confidence={conf}")
                    if not det:
                        log(f"  Agent says NOT deterministic, skipping issue creation")
                        summary.append({
                            "workflow_name": job["workflow_name"],
                            "job": job["job_name"],
                            "action": "agent_skipped",
                            "reason": agent_result.get("reason", "not deterministic"),
                        })
                        continue

            agent_suggested = (agent_result or {}).get("suggested_owners", [])
            owners = resolve_owners(
                job["workflow_name"], job["job_name"],
                owners_json, pipeline_owners,
                codeowners=codeowners,
                agent_suggested=agent_suggested,
                slack_directory=slack_directory,
                github_token=os.environ.get("GITHUB_TOKEN"),
            )
            owner_names = [o.get("name") or o["id"] for o in owners]
            log(f"  New failure: {job['job_name']} (owners: {owner_names})")

            if not agent_result or not agent_result.get("issue_body"):
                log(f"  Agent returned no issue body for {job['job_name']}, skipping")
                summary.append({
                    "workflow_name": job["workflow_name"],
                    "job": job["job_name"],
                    "action": "agent_skipped",
                    "reason": "no issue body from agent",
                })
                continue

            if not CREATE_ISSUES:
                log("  Dry run -- would create issue")
                summary.append({
                    "workflow_name": job["workflow_name"],
                    "job": job["job_name"],
                    "action": "dry_run",
                    "owners": owner_names,
                })
                continue

            issue_url, issue_title, issue_body = create_issue(job, agent_result)
            send_slack_notification(job, issue_url, owners)
            created_so_far += 1
            issue_number = issue_url.rsplit("/", 1)[-1]
            # Append to the in-memory list so subsequent jobs in this same run
            # see it as already-tracked and don't create duplicates.
            open_issues.append({"number": issue_number, "title": issue_title, "body": issue_body, "url": issue_url})
            signature = agent_result.get("signature", "")
            summary.append({
                "workflow_name": job["workflow_name"],
                "job": job["job_name"],
                "action": "created",
                "issue": issue_url,
                "owners": owner_names,
                "signature": signature,
            })

    # Step 7: Render summary
    # "skipped" no longer appears here -- tracked jobs are filtered before
    # find_failing_jobs returns, so the count comes from tracked_keys instead.
    skipped_count = len(tracked_pairs)
    log(f"\nDone: {created_so_far} created, {skipped_count} already-tracked, {len(failing_jobs)} new failures")

    md = render(summary, open_issues, ISSUE_REPO)
    if SUMMARY_OUTPUT:
        Path(SUMMARY_OUTPUT).write_text(md, encoding="utf-8")
        log(f"  Summary written to {SUMMARY_OUTPUT}")
    else:
        print(md)

    # Also write JSON summary to stderr for workflow logs
    print(json.dumps({
        "created": created_so_far,
        "skipped": skipped_count,
        "failures": summary,
    }, indent=2), file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
