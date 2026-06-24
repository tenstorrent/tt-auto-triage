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
_AGENT_TIMEOUT = 300


def _run_copilot_agent(prompt: str) -> str:
    """Run the Copilot CLI agent and return its stdout."""
    safe_env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL")
        if key in os.environ
    }
    copilot_pat = os.environ.get("COPILOT_PAT", "")
    if copilot_pat:
        safe_env["COPILOT_GITHUB_TOKEN"] = copilot_pat
    proc = subprocess.run(
        ["copilot", "-p", prompt, "--allow-all-tools"],
        capture_output=True,
        text=True,
        timeout=_AGENT_TIMEOUT,
        env=safe_env,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Copilot agent exited with code {proc.returncode}: {proc.stderr[:200]}"
        )
    return proc.stdout or ""


def _run_llm_agent(prompt: str) -> str:
    """Run the LLM agent (Copilot) and return its stdout."""
    return _run_copilot_agent(prompt)


def _parse_agent_json(text: str) -> dict[str, Any]:
    index = text.rfind(MARKER)
    if index < 0:
        raise ValueError(f"Marker {MARKER!r} not found in agent output")
    payload = text[index + len(MARKER):].strip()
    if payload.startswith("```"):
        payload = payload.split("\n", 1)[-1]  # skip ```[lang] line
        if payload.rstrip().endswith("```"):
            payload = payload.rstrip()[:-3]
    return json.loads(payload.strip())


def draft_issue_body(
    job: dict[str, Any],
    log_paths: list[str],
    consecutive: int = 3,
) -> dict[str, Any] | None:
    workflow_name = job["workflow_name"]
    job_name = job["job_name"]
    job_urls = job.get("job_urls", [])
    run_urls = job.get("run_urls", [])
    log_sections: list[str] = []
    run_log_entries = job.get("run_log_entries", [])
    if run_log_entries:
        for entry in run_log_entries:
            url = str(entry.get("job_url", "")).strip()
            path = str(entry.get("log_path", "")).strip()
            run_index = entry.get("run_index")
            if not url or not path:
                continue
            if isinstance(run_index, int) and run_index > 0:
                index = run_index
            else:
                index = len(log_sections) + 1
            log_sections.append(f"Run {index} job URL: {url}\nRun {index} local log path: {path}")
    else:
        non_empty_job_urls = [url for url in job_urls if url]
        for index, (url, path) in enumerate(zip(non_empty_job_urls, log_paths), start=1):
            log_sections.append(f"Run {index} job URL: {url}\nRun {index} local log path: {path}")

    # ── Build regression timeline section from boundary data ──────
    timeline_lines: list[str] = []
    if job.get("last_passing_sha"):
        sha = job["last_passing_sha"][:12]
        date = job.get("last_passing_date", "N/A")
        url = job.get("last_passing_url", "")
        timeline_lines.append(f"- Last passing run: commit `{sha}` on {date}" + (f" — {url}" if url else ""))
    if job.get("first_failing_sha"):
        sha = job["first_failing_sha"][:12]
        date = job.get("first_failing_date", "N/A")
        url = job.get("first_failing_url", "")
        timeline_lines.append(f"- First failing run: commit `{sha}` on {date}" + (f" — {url}" if url else ""))
    if job.get("last_failing_sha"):
        sha = job["last_failing_sha"][:12]
        date = job.get("last_failing_date", "N/A")
        url = job.get("last_failing_url", "")
        timeline_lines.append(f"- Most recent failing run: commit `{sha}` on {date}" + (f" — {url}" if url else ""))

    regression_timeline = "\n".join(timeline_lines) if timeline_lines else "No temporal boundary data available."

    prompt = _PROMPT_TEMPLATE.substitute(
        workflow_name=workflow_name,
        job_name=job_name,
        run_url_list="\n".join(f"  - Run {i}: {u}" for i, u in enumerate(run_urls, 1) if u),
        job_url_list="\n".join(f"  - Run {i}: {u}" for i, u in enumerate(job_urls, 1) if u),
        consecutive=consecutive,
        log_sections="\n".join(f"- {section}" for section in log_sections),
        marker=MARKER,
        regression_timeline=regression_timeline,
    )
    try:
        output = _run_llm_agent(prompt)
        result = _parse_agent_json(output)
        if not isinstance(result, dict):
            log("  Agent returned non-dict JSON, skipping")
            return None
        return result
    except subprocess.TimeoutExpired:
        log(f"  LLM agent timed out after {_AGENT_TIMEOUT}s")
        return None
    except Exception as exc:
        log(f"  Agent drafting failed ({type(exc).__name__}): {exc}")
        return None
