"""Use Cursor CLI agent to draft issue bodies by analyzing job logs."""

from __future__ import annotations

import json
import string
import subprocess
from pathlib import Path
from typing import Any

from .helpers import log

_PROMPT_TEMPLATE = string.Template(
    (Path(__file__).parent / "prompts" / "draft_issue.txt").read_text()
)

MARKER = "===FINAL_REVIEW==="
_DEFAULT_MODEL = "claude-4-sonnet"
_AGENT_TIMEOUT = 300


def _run_cursor_agent(prompt: str, model: str = _DEFAULT_MODEL) -> str:
    cmd = ["agent", "--trust", "-p", prompt]
    if model != "auto":
        cmd[1:1] = ["--model", model]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_AGENT_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Cursor agent exited with code {proc.returncode}: {proc.stderr[:200]}"
        )
    return proc.stdout or ""


def _parse_agent_json(text: str) -> dict[str, Any]:
    idx = text.rfind(MARKER)
    if idx < 0:
        raise ValueError(f"Marker {MARKER!r} not found in agent output")
    payload = text[idx + len(MARKER) :].strip()
    # Strip optional fenced code block (```[lang]\n...\n```)
    # Use rfind so that closing-fence detection works for both compact and
    # pretty-printed JSON: the outermost ``` is always the rightmost one.
    if payload.startswith("```"):
        first_nl = payload.index("\n")
        payload = payload[first_nl + 1 :]
        close_idx = payload.rfind("\n```")
        if close_idx >= 0:
            payload = payload[:close_idx]
    return json.loads(payload.strip())


def draft_issue_body(
    job: dict[str, Any],
    log_paths: list[str],
    model: str = _DEFAULT_MODEL,
    codeowners_path: str = "",
    consecutive: int = 3,
) -> dict[str, Any] | None:
    """Ask Cursor agent to analyze logs and draft an issue body.

    Returns a dict with keys: deterministic, confidence, signature,
    issue_title, issue_body, error_excerpt, suggested_owners.
    Returns None on failure.
    """
    workflow_name = job["workflow_name"]
    job_name = job["job_name"]
    job_urls = job.get("job_urls", [])
    run_urls = job.get("run_urls", [])

    log_sections: list[str] = []
    for idx, (url, path) in enumerate(zip(job_urls, log_paths), start=1):
        log_sections.append(
            f"Run {idx} job URL: {url}\nRun {idx} local log path: {path}"
        )

    run_url_list = "\n".join(
        f"  - Run {i}: {u}" for i, u in enumerate(run_urls, 1) if u
    )
    job_url_list = "\n".join(
        f"  - Run {i}: {u}" for i, u in enumerate(job_urls, 1) if u
    )

    codeowners_section = ""
    if codeowners_path:
        codeowners_section = (
            f"A CODEOWNERS file is available at: {codeowners_path}\n"
            "Read this file to identify GitHub usernames who own the failing test paths or\n"
            "workflow file. Ignore team handles (containing '/') and @tenstorrent/codeowner-bypass."
        )

    prompt = _PROMPT_TEMPLATE.substitute(
        workflow_name=workflow_name,
        job_name=job_name,
        run_url_list=run_url_list,
        job_url_list=job_url_list,
        consecutive=consecutive,
        log_sections="\n".join(f"- {s}" for s in log_sections),
        codeowners_section=codeowners_section,
        marker=MARKER,
    )
    try:
        output = _run_cursor_agent(prompt, model)
        result = _parse_agent_json(output)
        if not isinstance(result, dict):
            log("  Agent returned non-dict JSON, skipping")
            return None
        return result
    except subprocess.TimeoutExpired:
        log(f"  Cursor agent timed out after {_AGENT_TIMEOUT}s")
        return None
    except Exception as exc:
        log(f"  Agent drafting failed ({type(exc).__name__}): {exc}")
        return None
