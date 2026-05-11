#!/usr/bin/env python3
"""Issue lifecycle manager: close auto-triage issues that are no longer relevant.

All configuration is via environment variables:
  TARGET_REPO       -- repo to check workflow runs against (default: tenstorrent/tt-metal)
  ISSUE_REPO        -- repo containing open auto-triage issues (default: ebanerjeeTT/issue_dump)
  GITHUB_TOKEN      -- token with read access to TARGET_REPO
  ISSUE_WRITE_TOKEN -- token with write access to ISSUE_REPO
  RUNS_TO_EVALUATE  -- how many recent runs to fetch and require consensus across (default: 3)
  CLOSE_ISSUES      -- "true" to actually close issues; default "false" (dry-run)
  SUMMARY_OUTPUT    -- path to write markdown summary; default stdout
  CURSOR_API_KEY    -- required for agent analysis when llm-backend=cursor
  CURSOR_MODEL      -- Cursor model to use (default: claude-4-sonnet)
  LLM_BACKEND       -- LLM backend: 'copilot' (default) or 'cursor'
  CHECK_PASSING_ONLY -- if 'true', skip agent; only close jobs that are passing
"""

from __future__ import annotations

import json
import os
import re
import string
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.ci.helpers import gh, log
from tools.ci.check_job_status import (
    JobStatus,
    check_job_status,
    fetch_workflow_yaml,
    get_most_recent_run_id,
    get_recent_failing_run_jobs,
    get_recent_passing_runs,
    workflow_file_for,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TARGET_REPO = os.environ.get("TARGET_REPO", "tenstorrent/tt-metal")
ISSUE_REPO = os.environ.get("ISSUE_REPO", "tenstorrent/tt-metal")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
ISSUE_WRITE_TOKEN = os.environ.get("ISSUE_WRITE_TOKEN", "")
RUNS_TO_EVALUATE = int(os.environ.get("RUNS_TO_EVALUATE", "3"))
CLOSE_ISSUES = os.environ.get("CLOSE_ISSUES", "false").lower() == "true"
SUMMARY_OUTPUT = os.environ.get("SUMMARY_OUTPUT", "")
CURSOR_MODEL = os.environ.get("CURSOR_MODEL", "claude-4-sonnet")
LLM_BACKEND = os.environ.get("LLM_BACKEND", "copilot")
CHECK_PASSING_ONLY = os.environ.get("CHECK_PASSING_ONLY", "false").lower() == "true"

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
    safe_env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "CURSOR_API_KEY")
        if key in os.environ
    }
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_AGENT_TIMEOUT, env=safe_env)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Cursor agent exited {proc.returncode}: {proc.stderr[:200]}"
        )
    return proc.stdout or ""


def _run_copilot_agent(prompt: str) -> str:
    safe_env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "COPILOT_GITHUB_TOKEN")
        if key in os.environ
    }
    proc = subprocess.run(
        ["copilot", "-p", prompt, "--allow-all-tools"],
        capture_output=True,
        text=True,
        timeout=_AGENT_TIMEOUT,
        env=safe_env,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Copilot agent exited {proc.returncode}: {proc.stderr[:200]}"
        )
    return proc.stdout or ""


def _run_llm_agent(prompt: str) -> str:
    """Dispatch to the configured LLM backend."""
    if LLM_BACKEND == "copilot":
        return _run_copilot_agent(prompt)
    return _run_cursor_agent(prompt)


def _can_use_agent() -> bool:
    """Return True if the configured LLM backend is available."""
    if LLM_BACKEND == "cursor":
        if not os.environ.get("CURSOR_API_KEY"):
            log("  CURSOR_API_KEY not set — cannot invoke agent, keeping issue open")
            return False
    elif LLM_BACKEND == "copilot":
        import shutil
        if not shutil.which("copilot"):
            log("  Copilot CLI not found on PATH — cannot invoke agent, keeping issue open")
            return False
    return True


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
            workflow_name, job_name, TARGET_REPO, token, RUNS_TO_EVALUATE
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
        workflow_name, job_name, TARGET_REPO, token, RUNS_TO_EVALUATE
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


def extract_issue_run_ids(body: str) -> set[int]:
    """Extract GitHub Actions run IDs embedded in the issue body.

    The auto-issues branch stores run URLs like:
      https://github.com/.../actions/runs/12345678
    We parse all of them so we can compare against the current newest run.
    """
    return {
        int(m) for m in re.findall(r"/actions/runs/(\d+)", body)
    }


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


def _edit_issue_body(issue_number: int, new_body: str, token: str) -> None:
    gh(
        "issue", "edit", str(issue_number),
        f"--repo={ISSUE_REPO}",
        f"--body={new_body}",
        token=token,
    )


_FAILING_URLS_RE = re.compile(
    r"(### Failing job URLs \(last \d+ runs\)\n)"
    r"((?:- https://github\.com/[^\n]+\n?)+)",
)


def _replace_failing_urls(body: str, new_urls: list[str], count: int) -> str | None:
    """Replace the 'Failing job URLs' section in the issue body.

    Returns the updated body, or None if the section wasn't found.
    """
    new_section = f"### Failing job URLs (last {count} runs)\n"
    new_section += "".join(f"- {url}\n" for url in new_urls)

    result, subs = _FAILING_URLS_RE.subn(new_section, body, count=1)
    return result if subs > 0 else None


def _update_failing_urls(
    issue_number: int,
    body: str,
    failing_runs: list[dict[str, Any]],
    token: str,
) -> str | None:
    """Update the issue body with new failing job URLs and post a timestamp comment.

    Returns the updated body, or None if no update was made.
    """
    new_urls = [run["job_url"] for run in failing_runs if run.get("job_url")]
    if not new_urls:
        return None

    updated_body = _replace_failing_urls(body, new_urls, len(new_urls))
    if updated_body is None:
        log(f"  #{issue_number}: could not find 'Failing job URLs' section in body -- skipping update")
        return None

    if updated_body == body:
        log(f"  #{issue_number}: failing URLs unchanged -- skipping update")
        return None

    _edit_issue_body(issue_number, updated_body, token)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    _post_comment(issue_number, f"Failing job URLs updated. Last updated: {now}", token)
    log(f"  #{issue_number}: updated failing URLs ({len(new_urls)} links)")
    return updated_body


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
    """Build the prompt, run the LLM agent, parse and return its JSON."""
    log_sections = "\n".join(
        f"- Run {i} local path: {p}" for i, p in enumerate(log_paths, 1)
    )
    prompt = _PROMPT_TEMPLATE.substitute(
        workflow_name=workflow_name,
        job_name=job_name,
        api_status=status,
        runs_to_evaluate=RUNS_TO_EVALUATE,
        issue_body=issue_body,
        log_sections=log_sections,
        marker=MARKER,
    )
    try:
        output = _run_llm_agent(prompt)
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
        consecutive=RUNS_TO_EVALUATE,
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

    # Step 2: Check whether any runs have happened since the issue was created.
    # If the most recent workflow run was already in the issue's original run
    # list, there is no new information -- the agent would be comparing the
    # issue against itself, which is meaningless.
    issue_run_ids = extract_issue_run_ids(body)
    if issue_run_ids:
        newest_run_id = get_most_recent_run_id(
            workflow_name, TARGET_REPO, GITHUB_TOKEN or None
        )
        if newest_run_id is not None and newest_run_id in issue_run_ids:
            log(f"  #{number}: most recent run ({newest_run_id}) is already in the issue -- no new data, skipping")
            return {
                "number": number, "title": title, "url": url,
                "workflow": workflow_name, "job": job_name,
                "action": "kept_open",
                "reason": "no new workflow runs since issue was created",
            }

    # Step 2b: Update failing job URLs in the issue body if still failing.
    if status == JobStatus.STILL_FAILING and ISSUE_WRITE_TOKEN:
        failing_runs = get_recent_failing_run_jobs(
            workflow_name, job_name, TARGET_REPO,
            GITHUB_TOKEN or None, RUNS_TO_EVALUATE,
        )
        updated = _update_failing_urls(number, body, failing_runs, ISSUE_WRITE_TOKEN)
        if updated is not None:
            body = updated

    # CHECK_PASSING_ONLY fast path: deterministic close for passing jobs, no agent.
    if CHECK_PASSING_ONLY:
        if status == JobStatus.RESOLVED:
            comment = (
                f"Closing: job `{job_name}` in workflow `{workflow_name}` has been passing "
                f"for the last {RUNS_TO_EVALUATE} consecutive runs.\n\n"
                f"*Closed by tt-auto-triage lifecycle bot.*"
            )
            if CLOSE_ISSUES:
                # Skip the comment in check-passing-only mode to avoid flooding
                # issue authors with notifications; the Step Summary captures the details.
                _close_issue(number, ISSUE_WRITE_TOKEN)
                log(f"  #{number}: ✅ CLOSED (check-passing-only)")
            else:
                log(f"  #{number}: 🔍 DRY-RUN would close (check-passing-only)")
            return {
                "number": number, "title": title, "url": url,
                "workflow": workflow_name, "job": job_name,
                "action": "closed" if CLOSE_ISSUES else "dry_run_close",
                "status": status,
                "agent_reason": "check-passing-only: job passed all consecutive runs",
            }
        else:
            log(f"  #{number}: ⏭️  status is {status}, not resolved — keeping open (check-passing-only)")
            return {
                "number": number, "title": title, "url": url,
                "workflow": workflow_name, "job": job_name,
                "action": "kept_open",
                "reason": f"check-passing-only: status is {status}",
            }

    # Step 3: STILL_FAILING, RESOLVED, or REMOVED -- agent must analyze.
    if not _can_use_agent():
        return {
            "number": number, "title": title, "url": url,
            "workflow": workflow_name, "job": job_name,
            "action": "kept_open",
            "reason": f"{LLM_BACKEND} agent not available -- analysis required before closing",
        }

    # Step 4: Download evidence for the agent.
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

    # Step 5: Run the agent.
    log(f"  #{number}: running {LLM_BACKEND} agent...")
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
    log(f"Runs to evaluate: {RUNS_TO_EVALUATE}")
    log(f"LLM backend:   {LLM_BACKEND}")
    log(f"Check passing only: {CHECK_PASSING_ONLY}")
    if not CHECK_PASSING_ONLY and not _can_use_agent():
        log("  WARNING: agent backend not available -- all potentially-resolved issues will stay open")

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
