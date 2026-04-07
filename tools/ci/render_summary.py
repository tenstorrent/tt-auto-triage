"""Render markdown summary of auto-issue creation results."""

from __future__ import annotations

import json
import re
from typing import Any

from .helpers import gh, log


def _extract_signature_snippet(body: str, max_len: int = 80) -> str:
    """Try to pull a short error signature from an issue body."""
    m = re.search(r"Auto-triage-fingerprint:\s*(\S+)", body)
    fp = m.group(1) if m else ""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("RuntimeError") or stripped.startswith("##[error]"):
            return stripped[:max_len]
        if "TT_FATAL" in stripped:
            return stripped[:max_len]
    return fp[:max_len] if fp else ""


def render(
    summary: list[dict[str, Any]],
    open_issues: list[dict[str, Any]],
    issue_repo: str,
) -> str:
    lines: list[str] = []
    lines.append("# Auto-Issue Creation Summary\n")

    created = [s for s in summary if s.get("action") == "created"]
    skipped = [s for s in summary if s.get("action") == "skipped"]
    dry_run = [s for s in summary if s.get("action") == "dry_run"]

    if created:
        lines.append(f"## Created ({len(created)})\n")
        for item in created:
            wf = item.get("workflow_name", "")
            jn = item.get("job", "")
            url = item.get("issue", "")
            sig = item.get("signature", "")
            sig_display = f" -- `{sig[:60]}`" if sig else ""
            lines.append(f"- [{wf} / {jn}]({url}){sig_display}")
        lines.append("")

    if skipped:
        lines.append(f"## Skipped -- already tracked ({len(skipped)})\n")
        for item in skipped:
            wf = item.get("workflow_name", "")
            jn = item.get("job", "")
            existing = item.get("existing", "")
            lines.append(f"- [{wf} / {jn}]({existing})")
        lines.append("")

    if dry_run:
        lines.append(f"## Dry run -- would create ({len(dry_run)})\n")
        for item in dry_run:
            wf = item.get("workflow_name", "")
            jn = item.get("job", "")
            owners = item.get("owners", [])
            owner_str = ", ".join(owners) if owners else "no owners"
            lines.append(f"- {wf} / {jn} (owners: {owner_str})")
        lines.append("")

    if open_issues:
        lines.append(f"## All Open Tracked Issues ({len(open_issues)})\n")
        lines.append("| Workflow / Job | Issue | Signature |")
        lines.append("|----------------|-------|-----------|")
        for issue in open_issues:
            title = issue.get("title", "")
            url = issue.get("url", "")
            number = issue.get("number", "")
            body = issue.get("body", "")
            sig = _extract_signature_snippet(body)
            sig_cell = f"`{sig}`" if sig else ""
            lines.append(f"| {title} | [#{number}]({url}) | {sig_cell} |")
        lines.append("")

    return "\n".join(lines)


def load_all_open_issues(issue_repo: str, token: str) -> list[dict[str, Any]]:
    raw = gh(
        "issue", "list",
        f"--repo={issue_repo}", "--state=open", "--limit=200",
        "--json=number,title,body,url",
        "--label=CI auto triage",
        token=token,
    )
    return json.loads(raw)
