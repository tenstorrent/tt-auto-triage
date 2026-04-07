"""Use Cursor CLI agent to draft issue bodies by analyzing job logs."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from .helpers import log

MARKER = "===FINAL_REVIEW==="


def _run_cursor_agent(prompt: str, model: str = "claude-4-sonnet") -> str:
    cmd = ["cursor", "agent", "--trust", "-p", prompt]
    if model != "auto":
        cmd[2:2] = ["--model", model]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        log(f"  Cursor agent returned exit code {proc.returncode}")
        log(f"  stderr: {proc.stderr[:500]}")
    return proc.stdout or ""


def _parse_agent_json(text: str) -> dict[str, Any]:
    idx = text.rfind(MARKER)
    if idx < 0:
        raise ValueError(f"Marker {MARKER!r} not found in agent output")
    payload = text[idx + len(MARKER):].strip()
    # Strip fenced code blocks
    if payload.startswith("```"):
        first_nl = payload.index("\n")
        payload = payload[first_nl + 1:]
        if "```" in payload:
            payload = payload[:payload.rindex("```")]
    return json.loads(payload)


def draft_issue_body(
    job: dict[str, Any],
    log_paths: list[str],
    model: str = "claude-4-sonnet",
) -> dict[str, Any] | None:
    """Ask Cursor agent to analyze logs and draft an issue body.

    Returns a dict with keys: deterministic, confidence, signature,
    issue_title, issue_body, error_excerpt.  Returns None on failure.
    """
    workflow_name = job["workflow_name"]
    job_name = job["job_name"]
    job_urls = job.get("job_urls", [])
    run_urls = job.get("run_urls", [])

    log_sections: list[str] = []
    for idx, (url, path) in enumerate(zip(job_urls, log_paths), start=1):
        log_sections.append(f"Run {idx} job URL: {url}\nRun {idx} local log path: {path}")

    run_url_list = "\n".join(f"  - Run {i}: {u}" for i, u in enumerate(run_urls, 1) if u)
    job_url_list = "\n".join(f"  - Run {i}: {u}" for i, u in enumerate(job_urls, 1) if u)

    prompt = f"""\
You are reviewing a CI failure for deterministic issue creation.

Workflow: {workflow_name}
Job: {job_name}

Failing run URLs:
{run_url_list}

Failing job URLs:
{job_url_list}

Log files for each of the 3 failing runs are saved locally.
You MUST read each log file and determine the terminal failure.

Log file references:
{chr(10).join(f"- {section}" for section in log_sections)}

INSTRUCTIONS:
1. Read ALL three log files using your file reading tools.
2. Identify the terminal failure or error in each log.
3. Determine if all 3 runs fail with semantically identical errors.
4. If yes: draft a GitHub issue title and body in this exact format:
   - Title: a concise description of the failure (NOT just "deterministic failure")
   - Body: a markdown CI Failure Report with these sections:
     ## CI Failure Report
     **Workflow:** ...
     **Job:** ...
     ### Failing job URLs (last 3 runs)
     - (list each job URL)
     ### Failing test path(s)
     - (identify which test files/commands are failing, from log content)
     ### Error excerpt (terminal failure from logs)
     (fenced code blocks with the actual error messages from logs)
     ### Reproduction steps
     (how to reproduce locally based on the job command)
     ### Notes
     (any additional context, e.g. timeout vs assertion failure)
5. If the errors are NOT semantically identical across all 3 runs, set deterministic to false.

Output the marker below on its own line, followed by compact JSON only:
{MARKER}
{{"deterministic": true/false, "confidence": "low|medium|high", "signature": "short error signature", "error_excerpt": "key error text", "issue_title": "[CI] ...", "issue_body": "full markdown body"}}
"""
    try:
        output = _run_cursor_agent(prompt, model)
        result = _parse_agent_json(output)
        if not isinstance(result, dict):
            log("  Agent returned non-dict JSON, skipping")
            return None
        return result
    except Exception as exc:
        log(f"  Agent drafting failed: {exc}")
        return None
