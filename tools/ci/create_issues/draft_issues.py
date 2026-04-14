from __future__ import annotations

import json
import os
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
    safe_env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "CURSOR_API_KEY")
        if key in os.environ
    }
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=_AGENT_TIMEOUT,
        env=safe_env,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Cursor agent exited with code {proc.returncode}: {proc.stderr[:200]}"
        )
    return proc.stdout or ""


def _parse_agent_json(text: str) -> dict[str, Any]:
    index = text.rfind(MARKER)
    if index < 0:
        raise ValueError(f"Marker {MARKER!r} not found in agent output")
    payload = text[index + len(MARKER) :].strip()
    if payload.startswith("```"):
        payload = payload[3:]
        first_newline = payload.find("\n")
        if first_newline >= 0:
            payload = payload[first_newline + 1:]
        close_index = payload.rfind("```")
        if close_index >= 0 and payload[close_index:].strip() == "```":
            payload = payload[:close_index]
    return json.loads(payload.strip())


def draft_issue_body(
    job: dict[str, Any],
    log_paths: list[str],
    model: str = _DEFAULT_MODEL,
    consecutive: int = 3,
) -> dict[str, Any] | None:
    workflow_name = job["workflow_name"]
    job_name = job["job_name"]
    job_urls = job.get("job_urls", [])
    run_urls = job.get("run_urls", [])

    log_sections: list[str] = []
    for index, (url, path) in enumerate(zip(job_urls, log_paths), start=1):
        log_sections.append(f"Run {index} job URL: {url}\nRun {index} local log path: {path}")

    prompt = _PROMPT_TEMPLATE.substitute(
        workflow_name=workflow_name,
        job_name=job_name,
        run_url_list="\n".join(f"  - Run {i}: {u}" for i, u in enumerate(run_urls, 1) if u),
        job_url_list="\n".join(f"  - Run {i}: {u}" for i, u in enumerate(job_urls, 1) if u),
        consecutive=consecutive,
        log_sections="\n".join(f"- {section}" for section in log_sections),
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
