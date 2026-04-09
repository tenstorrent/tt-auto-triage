#!/usr/bin/env python3
"""Issue lifecycle manager: close auto-triage issues that are no longer relevant.

All configuration is via environment variables:
  TARGET_REPO      -- repo to check workflow runs against (default: tenstorrent/tt-metal)
  ISSUE_REPO       -- repo containing open auto-triage issues (default: ebanerjeeTT/issue_dump)
  GITHUB_TOKEN     -- token with read access to TARGET_REPO
  ISSUE_WRITE_TOKEN -- token with write access to ISSUE_REPO
  CONSECUTIVE_RUNS -- how many recent runs to evaluate (default: 3)
  CLOSE_ISSUES     -- "true" to actually close issues; default "false" (dry-run)
  SUMMARY_OUTPUT   -- path to write markdown summary; default stdout
  SLACK_BOT_TOKEN  -- Slack bot token (optional, for future notifications)
  SLACK_CHANNEL_ID -- Slack channel (optional)
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from tools.ci.helpers import gh, log, slack_post
from tools.ci.check_job_status import JobStatus, check_job_status
from tools.ci.find_fix_pr import find_fix_pr


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TARGET_REPO = os.environ.get("TARGET_REPO", "tenstorrent/tt-metal")
ISSUE_REPO = os.environ.get("ISSUE_REPO", "ebanerjeeTT/issue_dump")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
ISSUE_WRITE_TOKEN = os.environ.get("ISSUE_WRITE_TOKEN", "")
CONSECUTIVE_RUNS = int(os.environ.get("CONSECUTIVE_RUNS", "3"))
CLOSE_ISSUES = os.environ.get("CLOSE_ISSUES", "false").lower() == "true"
SUMMARY_OUTPUT = os.environ.get("SUMMARY_OUTPUT", "")
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "")

# Label applied to issues that resolved without a traceable fix PR.
LABEL_UNKNOWN_FIX = "solved by unknown change"


# ---------------------------------------------------------------------------
# Issue loading
# ---------------------------------------------------------------------------


def load_open_issues(issue_repo: str, token: str) -> list[dict[str, Any]]:
    raw = gh(
        "issue", "list",
        f"--repo={issue_repo}", "--state=open", "--limit=200",
        "--json=number,title,body,url,createdAt",
        "--label=CI auto triage",
        token=token,
    )
    return json.loads(raw)


def extract_markers(body: str) -> tuple[str, str]:
    """Extract (workflow_name, job_name) from issue body markers.

    Returns ("", "") if either marker is missing (issue is not in the new format).
    """
    wf_m = re.search(r"Auto-triage-workflow:\s*`?([^`\n]+)`?", body)
    jn_m = re.search(r"Auto-triage-job-name:\s*`?([^`\n]+)`?", body)
    if wf_m and jn_m:
        return wf_m.group(1).strip(), jn_m.group(1).strip()
    return "", ""


# ---------------------------------------------------------------------------
# Issue actions
# ---------------------------------------------------------------------------

_CLOSE_COMMENT_RESOLVED = """\
This issue has been automatically closed because the CI job is no longer \
failing in recent runs.

**Workflow:** `{workflow_name}`
**Job:** `{job_name}`
**Status in last {n} runs:** passing

Likely fixed by: {pr_url} _{pr_title}_

*Closed by tt-auto-triage lifecycle bot.*"""

_CLOSE_COMMENT_REMOVED = """\
This issue has been automatically closed because the job no longer exists \
in the workflow definition.

**Workflow:** `{workflow_name}`
**Job:** `{job_name}`

The job name `{job_name}` is completely absent from \
`.github/workflows/` — it was not merely disabled or commented out, \
indicating it was intentionally deleted.

Likely removed by: {pr_url} _{pr_title}_

*Closed by tt-auto-triage lifecycle bot.*"""

_UNKNOWN_FIX_COMMENT = """\
:mag: This job appears to no longer be failing in recent runs, but no \
clear fixing PR could be identified.

**Workflow:** `{workflow_name}`
**Job:** `{job_name}`
**Status in last {n} runs:** passing

The label **`{label}`** has been added. This issue will remain open \
until manually reviewed or a fixing PR is identified.

*Note by tt-auto-triage lifecycle bot.*"""


def _post_comment(issue_number: int, body: str, token: str) -> None:
    gh(
        "issue", "comment", str(issue_number),
        f"--repo={ISSUE_REPO}",
        f"--body={body}",
        token=token,
    )


def _close_issue(issue_number: int, token: str) -> None:
    gh(
        "issue", "close", str(issue_number),
        f"--repo={ISSUE_REPO}",
        token=token,
    )


def _add_label(issue_number: int, label: str, token: str) -> None:
    gh(
        "issue", "edit", str(issue_number),
        f"--repo={ISSUE_REPO}",
        f"--add-label={label}",
        token=token,
    )


# ---------------------------------------------------------------------------
# Per-issue processing
# ---------------------------------------------------------------------------


def process_issue(issue: dict[str, Any]) -> dict[str, Any]:
    """Evaluate one issue and return a summary dict describing the action taken."""
    number = issue["number"]
    title = issue.get("title", "")
    body = issue.get("body") or ""
    created_at = issue.get("createdAt", "")
    url = issue.get("url", f"#{number}")

    workflow_name, job_name = extract_markers(body)
    if not workflow_name or not job_name:
        log(f"  #{number}: skipping -- no Auto-triage-workflow/job-name markers")
        return {"number": number, "title": title, "url": url, "action": "skipped_no_markers"}

    log(f"  #{number}: checking '{workflow_name} / {job_name}'...")

    status = check_job_status(
        workflow_name, job_name, TARGET_REPO,
        token=GITHUB_TOKEN or None,
        consecutive=CONSECUTIVE_RUNS,
    )
    log(f"  #{number}: status = {status}")

    if status in (JobStatus.STILL_FAILING, JobStatus.DISABLED, JobStatus.UNKNOWN, JobStatus.SKIPPED):
        reason_map = {
            JobStatus.STILL_FAILING: "still failing",
            JobStatus.DISABLED: "job disabled in workflow (not deleted) -- failure may be hidden",
            JobStatus.SKIPPED: "job being skipped in recent runs -- failure may be hidden",
            JobStatus.UNKNOWN: "could not determine status",
        }
        return {
            "number": number, "title": title, "url": url,
            "workflow": workflow_name, "job": job_name,
            "action": "kept_open",
            "reason": reason_map.get(status, status),
        }

    # Job is RESOLVED or REMOVED -- find the fixing PR before closing.
    pr = find_fix_pr(
        workflow_name, job_name, created_at,
        TARGET_REPO, token=GITHUB_TOKEN or None,
    )

    if pr is None:
        # Can't identify a fix -- label and leave open rather than closing blindly.
        log(f"  #{number}: resolved but no fix PR found -- adding '{LABEL_UNKNOWN_FIX}' label")
        if CLOSE_ISSUES:
            _add_label(number, LABEL_UNKNOWN_FIX, ISSUE_WRITE_TOKEN)
            _post_comment(number, _UNKNOWN_FIX_COMMENT.format(
                workflow_name=workflow_name,
                job_name=job_name,
                n=CONSECUTIVE_RUNS,
                label=LABEL_UNKNOWN_FIX,
            ), ISSUE_WRITE_TOKEN)
        return {
            "number": number, "title": title, "url": url,
            "workflow": workflow_name, "job": job_name,
            "action": "labeled_unknown_fix",
            "status": status,
        }

    # We have a fix PR -- close the issue with an explanatory comment.
    pr_url = pr["url"]
    pr_title = pr["title"]

    if status == JobStatus.RESOLVED:
        comment = _CLOSE_COMMENT_RESOLVED.format(
            workflow_name=workflow_name,
            job_name=job_name,
            n=CONSECUTIVE_RUNS,
            pr_url=pr_url,
            pr_title=pr_title,
        )
    else:  # REMOVED
        comment = _CLOSE_COMMENT_REMOVED.format(
            workflow_name=workflow_name,
            job_name=job_name,
            pr_url=pr_url,
            pr_title=pr_title,
        )

    log(f"  #{number}: closing -- {status}, fixed by {pr_url}")
    if CLOSE_ISSUES:
        _post_comment(number, comment, ISSUE_WRITE_TOKEN)
        _close_issue(number, ISSUE_WRITE_TOKEN)

    return {
        "number": number, "title": title, "url": url,
        "workflow": workflow_name, "job": job_name,
        "action": "closed" if CLOSE_ISSUES else "dry_run_close",
        "status": status,
        "fix_pr": pr_url,
        "fix_pr_title": pr_title,
    }


# ---------------------------------------------------------------------------
# Summary rendering
# ---------------------------------------------------------------------------


def render_summary(results: list[dict[str, Any]], issue_repo: str) -> str:
    lines = ["# Issue Lifecycle Summary\n"]

    closed = [r for r in results if r["action"] in ("closed", "dry_run_close")]
    labeled = [r for r in results if r["action"] == "labeled_unknown_fix"]
    kept = [r for r in results if r["action"] == "kept_open"]
    skipped = [r for r in results if r["action"] == "skipped_no_markers"]

    if closed:
        verb = "Closed" if CLOSE_ISSUES else "Would close (dry run)"
        lines.append(f"## {verb} ({len(closed)})\n")
        for r in closed:
            status_tag = f"`{r.get('status', '')}`"
            fix = r.get("fix_pr", "")
            fix_str = f" — fixed by [{r.get('fix_pr_title', fix)}]({fix})" if fix else ""
            lines.append(f"- [#{r['number']}]({r['url']}) {r['title']} {status_tag}{fix_str}")
        lines.append("")

    if labeled:
        lines.append(f"## Labeled `{LABEL_UNKNOWN_FIX}` ({len(labeled)})\n")
        lines.append("These jobs appear resolved but no fix PR was found. Left open for manual review.\n")
        for r in labeled:
            lines.append(f"- [#{r['number']}]({r['url']}) {r['title']} (`{r.get('status', '')}`)")
        lines.append("")

    if kept:
        lines.append(f"## Kept open ({len(kept)})\n")
        for r in kept:
            lines.append(f"- [#{r['number']}]({r['url']}) {r['title']} — {r.get('reason', '')}")
        lines.append("")

    if skipped:
        lines.append(f"## Skipped (no markers) ({len(skipped)})\n")
        lines.append("These issues are missing `Auto-triage-workflow` / `Auto-triage-job-name` markers "
                     "and cannot be evaluated by this tool.\n")
        for r in skipped:
            lines.append(f"- [#{r['number']}]({r['url']}) {r['title']}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    log("=== Issue Lifecycle Manager ===")
    log(f"Target repo:   {TARGET_REPO}")
    log(f"Issue repo:    {ISSUE_REPO}")
    log(f"Close issues:  {CLOSE_ISSUES}")
    log(f"Runs to check: {CONSECUTIVE_RUNS}")

    issues = load_open_issues(ISSUE_REPO, ISSUE_WRITE_TOKEN)
    log(f"Loaded {len(issues)} open 'CI auto triage' issues")

    results: list[dict[str, Any]] = []
    for issue in issues:
        result = process_issue(issue)
        results.append(result)

    closed_count = sum(1 for r in results if r["action"] in ("closed", "dry_run_close"))
    labeled_count = sum(1 for r in results if r["action"] == "labeled_unknown_fix")
    kept_count = sum(1 for r in results if r["action"] == "kept_open")

    log(f"\nDone: {closed_count} closed, {labeled_count} labeled unknown-fix, {kept_count} kept open")

    md = render_summary(results, ISSUE_REPO)
    if SUMMARY_OUTPUT:
        Path(SUMMARY_OUTPUT).write_text(md, encoding="utf-8")
        log(f"Summary written to {SUMMARY_OUTPUT}")
    else:
        print(md)

    print(json.dumps({
        "closed": closed_count,
        "labeled_unknown_fix": labeled_count,
        "kept_open": kept_count,
        "results": results,
    }, indent=2), file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
