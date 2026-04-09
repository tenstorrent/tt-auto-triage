#!/usr/bin/env python3
"""Issue lifecycle manager: close auto-triage issues that are no longer relevant.

All configuration is via environment variables:
  TARGET_REPO       -- repo to check workflow runs against (default: tenstorrent/tt-metal)
  ISSUE_REPO        -- repo containing open auto-triage issues (default: ebanerjeeTT/issue_dump)
  GITHUB_TOKEN      -- token with read access to TARGET_REPO
  ISSUE_WRITE_TOKEN -- token with write access to ISSUE_REPO
  CONSECUTIVE_RUNS  -- how many recent runs to evaluate (default: 3)
  CLOSE_ISSUES      -- "true" to actually close issues; default "false" (dry-run)
  SUMMARY_OUTPUT    -- path to write markdown summary; default stdout
  CURSOR_API_KEY    -- required for agent analysis (issues stay open if absent)
  CURSOR_MODEL      -- Cursor model to use (default: claude-4-sonnet)
"""

from __future__ import annotations

import json
import os
import re
import string
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from tools.ci.helpers import gh, log
from tools.ci.check_job_status import (
    JobStatus,
    check_job_status,
    fetch_workflow_yaml,
    get_recent_failing_run_jobs,
    get_recent_passing_runs,
    workflow_file_for,
)


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
CURSOR_MODEL = os.environ.get("CURSOR_MODEL", "claude-4-sonnet")

LABEL_UNKNOWN_FIX = "solved by unknown change"

# Agent configuration — same marker/timeout pattern as the auto-issues branch.
MARKER = "===LIFECYCLE_REVIEW==="
_AGENT_TIMEOUT = 300

_PROMPT_TEMPLATE = string.Template(
    (Path(__file__).parent / "prompts" / "lifecycle_review.txt").read_text()
)


# ---------------------------------------------------------------------------
# Cursor agent
# ---------------------------------------------------------------------------


def _run_cursor_agent(prompt: str) -> str:
    # "--trust" allows the agent to read local log/YAML files without
    # interactive confirmation -- required for non-interactive CI.
    cmd = ["agent", "--trust", "--model", CURSOR_MODEL, "-p", prompt]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_AGENT_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Cursor agent exited {proc.returncode}: {proc.stderr[:200]}"
        )
    return proc.stdout or ""


def _parse_agent_json(text: str) -> dict[str, Any]:
    # rfind so we always get the last marker occurrence (agent may echo it
    # during reasoning before producing the real output).
    idx = text.rfind(MARKER)
    if idx < 0:
        raise ValueError(f"Marker {MARKER!r} not found in agent output")
    payload = text[idx + len(MARKER):].strip()
    if payload.startswith("```"):
        first_nl = payload.index("\n")
        payload = payload[first_nl + 1:]
        close_idx = payload.rfind("\n```")
        if close_idx >= 0:
            payload = payload[:close_idx]
    return json.loads(payload.strip())


# ---------------------------------------------------------------------------
# Log / evidence download for agent
# ---------------------------------------------------------------------------


def _download_evidence(
    workflow_name: str,
    job_name: str,
    status: str,
    logs_dir: Path,
) -> list[str]:
    """Download evidence files for the agent to analyze.

    For STILL_FAILING: download logs from recent failing runs so the agent
      can compare the current error against the original issue.
    For RESOLVED: download logs from recent passing runs.
    For REMOVED: fetch the current workflow YAML (agent verifies job is gone).
    Returns a list of local file paths.
    """
    token = GITHUB_TOKEN or None

    if status == JobStatus.REMOVED:
        wf_file = workflow_file_for(workflow_name, TARGET_REPO, token)
        if not wf_file:
            log(f"  Warning: could not find workflow file for '{workflow_name}'")
            return []
        try:
            yaml_text = fetch_workflow_yaml(wf_file, TARGET_REPO, token)
            out = logs_dir / f"workflow_{wf_file}"
            out.write_text(yaml_text, encoding="utf-8")
            log(f"  Saved workflow YAML to {out}")
            return [str(out)]
        except Exception as exc:
            log(f"  Warning: could not fetch workflow YAML: {exc}")
            return []

    safe_job = re.sub(r"[^a-zA-Z0-9_-]", "_", job_name)[:60]

    if status == JobStatus.STILL_FAILING:
        failing_runs = get_recent_failing_run_jobs(
            workflow_name, job_name, TARGET_REPO, token, CONSECUTIVE_RUNS
        )
        if not failing_runs:
            log(f"  Warning: no failing runs found for log download")
            return []
        paths: list[str] = []
        for idx, run_info in enumerate(failing_runs, start=1):
            run_id = run_info["run_id"]
            job_id = run_info["job_id"]
            out = logs_dir / f"{safe_job}__failing_run{idx}.txt"
            try:
                text = gh(
                    "run", "view", str(run_id),
                    f"--repo={TARGET_REPO}", "--job", str(job_id),
                    "--log-failed", token=token, timeout=60,
                )
                if not text.strip():
                    text = gh(
                        "run", "view", str(run_id),
                        f"--repo={TARGET_REPO}", "--job", str(job_id),
                        "--log", token=token, timeout=60,
                    )
                out.write_text(text, encoding="utf-8")
                paths.append(str(out))
                log(f"  Downloaded failing-run log: {out.name}")
            except Exception as exc:
                log(f"  Warning: could not download log for run {run_id}: {exc}")
        return paths

    # RESOLVED: download logs from recent passing runs.
    passing_runs = get_recent_passing_runs(
        workflow_name, job_name, TARGET_REPO, token, CONSECUTIVE_RUNS
    )
    if not passing_runs:
        log(f"  Warning: no passing runs found for log download")
        return []

    paths = []
    for idx, run_info in enumerate(passing_runs, start=1):
        run_id = run_info["run_id"]
        job_id = run_info["job_id"]
        out = logs_dir / f"{safe_job}__passing_run{idx}.txt"
        try:
            text = gh(
                "run", "view", str(run_id),
                f"--repo={TARGET_REPO}", "--job", str(job_id),
                "--log", token=token, timeout=60,
            )
            out.write_text(text, encoding="utf-8")
            paths.append(str(out))
            log(f"  Downloaded passing-run log: {out.name}")
        except Exception as exc:
            log(f"  Warning: could not download log for run {run_id}: {exc}")
    return paths


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
    """Extract (workflow_name, job_name) from issue body markers."""
    wf_m = re.search(r"Auto-triage-workflow:\s*`?([^`\n]+)`?", body)
    jn_m = re.search(r"Auto-triage-job-name:\s*`?([^`\n]+)`?", body)
    if wf_m and jn_m:
        return wf_m.group(1).strip(), jn_m.group(1).strip()
    return "", ""


# ---------------------------------------------------------------------------
# Issue actions
# ---------------------------------------------------------------------------


def _post_comment(issue_number: int, body: str, token: str) -> None:
    gh(
        "issue", "comment", str(issue_number),
        f"--repo={ISSUE_REPO}",
        f"--body={body}",
        token=token,
    )


def _close_issue(issue_number: int, token: str) -> None:
    gh("issue", "close", str(issue_number), f"--repo={ISSUE_REPO}", token=token)


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


def _call_agent(
    workflow_name: str,
    job_name: str,
    status: str,
    issue_body: str,
    log_paths: list[str],
) -> dict[str, Any] | None:
    """Build the prompt, run the Cursor agent, parse and return its JSON."""
    log_sections = "\n".join(
        f"- Run {i} local path: {p}" for i, p in enumerate(log_paths, 1)
    )
    prompt = _PROMPT_TEMPLATE.substitute(
        workflow_name=workflow_name,
        job_name=job_name,
        api_status=status,
        consecutive_runs=CONSECUTIVE_RUNS,
        issue_body=issue_body,
        log_sections=log_sections,
        marker=MARKER,
    )
    try:
        output = _run_cursor_agent(prompt)
        result = _parse_agent_json(output)
        if not isinstance(result, dict):
            log("  Agent returned non-dict JSON")
            return None
        return result
    except subprocess.TimeoutExpired:
        log(f"  Agent timed out after {_AGENT_TIMEOUT}s")
        return None
    except Exception as exc:
        log(f"  Agent failed ({type(exc).__name__}): {exc}")
        return None


def process_issue(issue: dict[str, Any], logs_dir: Path) -> dict[str, Any]:
    """Evaluate one issue and return a summary dict describing the action taken."""
    number = issue["number"]
    title = issue.get("title", "")
    body = issue.get("body") or ""
    url = issue.get("url", f"#{number}")

    workflow_name, job_name = extract_markers(body)
    if not workflow_name or not job_name:
        log(f"  #{number}: skipping -- no Auto-triage-workflow/job-name markers")
        return {"number": number, "title": title, "url": url, "action": "skipped_no_markers"}

    log(f"  #{number}: checking '{workflow_name} / {job_name}'...")

    # Step 1: cheap API-based preliminary filter.
    status = check_job_status(
        workflow_name, job_name, TARGET_REPO,
        token=GITHUB_TOKEN or None,
        consecutive=CONSECUTIVE_RUNS,
    )
    log(f"  #{number}: API status = {status}")

    # DISABLED/UNKNOWN/SKIPPED: no useful evidence to give the agent, keep open.
    if status in (JobStatus.DISABLED, JobStatus.UNKNOWN, JobStatus.SKIPPED):
        reason_map = {
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

    # Step 2: STILL_FAILING, RESOLVED, or REMOVED -- agent must analyze.
    if not os.environ.get("CURSOR_API_KEY"):
        log(f"  #{number}: CURSOR_API_KEY missing -- keeping open (no agent, no close)")
        return {
            "number": number, "title": title, "url": url,
            "workflow": workflow_name, "job": job_name,
            "action": "kept_open",
            "reason": "CURSOR_API_KEY not set -- agent analysis required before closing",
        }

    # Step 3: Download evidence for the agent.
    log(f"  #{number}: downloading evidence for agent...")
    log_paths = _download_evidence(workflow_name, job_name, status, logs_dir)
    if not log_paths:
        log(f"  #{number}: no evidence files -- keeping open")
        return {
            "number": number, "title": title, "url": url,
            "workflow": workflow_name, "job": job_name,
            "action": "kept_open",
            "reason": "could not download evidence for agent analysis",
        }

    # Step 4: Run the agent.
    log(f"  #{number}: running Cursor agent...")
    agent_result = _call_agent(workflow_name, job_name, status, body, log_paths)

    if agent_result is None:
        log(f"  #{number}: agent failed -- keeping open")
        return {
            "number": number, "title": title, "url": url,
            "workflow": workflow_name, "job": job_name,
            "action": "kept_open",
            "reason": "agent analysis failed",
        }

    should_close = agent_result.get("should_close", False)
    reason = agent_result.get("reason", "")
    comment_body = agent_result.get("comment_body", "")
    fix_pr_hint = agent_result.get("fix_pr_hint", "")

    log(f"  #{number}: agent decision: should_close={should_close}, fix_pr_hint={fix_pr_hint!r}")

    # Step 5: Agent says keep open.
    if not should_close:
        return {
            "number": number, "title": title, "url": url,
            "workflow": workflow_name, "job": job_name,
            "action": "agent_kept_open",
            "reason": reason,
        }

    # Step 6: Agent says close but couldn't identify a fix PR.
    if not fix_pr_hint:
        log(f"  #{number}: resolved but no fix PR -- adding '{LABEL_UNKNOWN_FIX}' label")
        if CLOSE_ISSUES:
            _add_label(number, LABEL_UNKNOWN_FIX, ISSUE_WRITE_TOKEN)
            _post_comment(number, comment_body, ISSUE_WRITE_TOKEN)
        return {
            "number": number, "title": title, "url": url,
            "workflow": workflow_name, "job": job_name,
            "action": "labeled_unknown_fix" if CLOSE_ISSUES else "dry_run_label",
            "status": status,
            "agent_reason": reason,
        }

    # Step 7: Agent says close and found the fix -- close with agent-drafted comment.
    log(f"  #{number}: closing (fixed by {fix_pr_hint})")
    if CLOSE_ISSUES:
        _post_comment(number, comment_body, ISSUE_WRITE_TOKEN)
        _close_issue(number, ISSUE_WRITE_TOKEN)

    return {
        "number": number, "title": title, "url": url,
        "workflow": workflow_name, "job": job_name,
        "action": "closed" if CLOSE_ISSUES else "dry_run_close",
        "status": status,
        "fix_pr": fix_pr_hint,
        "agent_reason": reason,
    }


# ---------------------------------------------------------------------------
# Summary rendering
# ---------------------------------------------------------------------------


def render_summary(results: list[dict[str, Any]]) -> str:
    lines = ["# Issue Lifecycle Summary\n"]

    closed = [r for r in results if r["action"] in ("closed", "dry_run_close")]
    labeled = [r for r in results if r["action"] in ("labeled_unknown_fix", "dry_run_label")]
    agent_kept = [r for r in results if r["action"] == "agent_kept_open"]
    kept = [r for r in results if r["action"] == "kept_open"]
    skipped = [r for r in results if r["action"] == "skipped_no_markers"]

    if closed:
        verb = "Closed" if CLOSE_ISSUES else "Would close (dry run)"
        lines.append(f"## {verb} ({len(closed)})\n")
        for r in closed:
            fix = r.get("fix_pr", "")
            fix_str = f" — fix: {fix}" if fix else ""
            lines.append(f"- [#{r['number']}]({r['url']}) {r['title']}{fix_str}")
        lines.append("")

    if labeled:
        verb = "Labeled" if CLOSE_ISSUES else "Would label (dry run)"
        lines.append(f"## {verb} `{LABEL_UNKNOWN_FIX}` ({len(labeled)})\n")
        lines.append("Agent confirmed resolved but could not identify the fix PR. Left open for manual review.\n")
        for r in labeled:
            reason = r.get("agent_reason", "")
            lines.append(f"- [#{r['number']}]({r['url']}) {r['title']} — _{reason}_")
        lines.append("")

    if agent_kept:
        lines.append(f"## Kept open (agent decision) ({len(agent_kept)})\n")
        lines.append("API suggested resolved but agent found evidence the failure persists.\n")
        for r in agent_kept:
            lines.append(f"- [#{r['number']}]({r['url']}) {r['title']} — _{r.get('reason', '')}_")
        lines.append("")

    if kept:
        lines.append(f"## Kept open (still failing) ({len(kept)})\n")
        for r in kept:
            lines.append(f"- [#{r['number']}]({r['url']}) {r['title']} — {r.get('reason', '')}")
        lines.append("")

    if skipped:
        lines.append(f"## Skipped (no markers) ({len(skipped)})\n")
        lines.append("Missing `Auto-triage-workflow` / `Auto-triage-job-name` markers.\n")
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
    if not os.environ.get("CURSOR_API_KEY"):
        log("  WARNING: CURSOR_API_KEY not set -- all potentially-resolved issues will stay open")

    issues = load_open_issues(ISSUE_REPO, ISSUE_WRITE_TOKEN)
    log(f"Loaded {len(issues)} open 'CI auto triage' issues")

    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        logs_dir = Path(tmpdir) / "lifecycle_logs"
        logs_dir.mkdir()
        for issue in issues:
            result = process_issue(issue, logs_dir)
            results.append(result)

    closed_count = sum(1 for r in results if r["action"] in ("closed", "dry_run_close"))
    labeled_count = sum(1 for r in results if r["action"] in ("labeled_unknown_fix", "dry_run_label"))
    kept_count = sum(1 for r in results if r["action"] in ("kept_open", "agent_kept_open"))

    log(f"\nDone: {closed_count} closed, {labeled_count} labeled unknown-fix, {kept_count} kept open")

    md = render_summary(results)
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
