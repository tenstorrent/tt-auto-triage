from __future__ import annotations

import re
from typing import Any

from .helpers import api_get, paginate_api
from .issue_state import AUTO_TRIAGE_LABEL


def _extract_signature_snippet(body: str, max_len: int = 80) -> str:
    match = re.search(r"Auto-triage-fingerprint:\s*`?([^`\s]+)`?", body)
    fingerprint = match.group(1) if match else ""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(("RuntimeError", "##[error]")) or "TT_FATAL" in stripped:
            return stripped[:max_len]
    return fingerprint[:max_len] if fingerprint else ""


def load_all_open_issues(issue_repo: str, token: str) -> list[dict[str, Any]]:
    owner, repo = issue_repo.split("/", 1)
    label = AUTO_TRIAGE_LABEL.replace(" ", "%20")
    base_url = f"https://api.github.com/repos/{owner}/{repo}/issues?state=open&labels={label}&per_page=100"
    issues: list[dict[str, Any]] = []
    page = 1
    while True:
        data = api_get(f"{base_url}&page={page}", token)
        if not data:
            break
        issues += [
            {"number": i.get("number", ""), "title": i.get("title", ""), "body": i.get("body", ""), "url": i.get("html_url", "")}
            for i in data if "pull_request" not in i
        ]
        if len(data) < 100:
            break
        page += 1
    return issues


def render(summary: list[dict[str, Any]], open_issues: list[dict[str, Any]]) -> str:
    lines: list[str] = ["# Auto-Issue Creation Summary\n"]
    created = [item for item in summary if item.get("action") == "created"]
    dry_run = [item for item in summary if item.get("action") == "dry_run"]
    skipped = [item for item in summary if item.get("action") == "agent_skipped"]

    if created:
        lines.append(f"## Created ({len(created)})\n")
        for item in created:
            signature = item.get("signature", "")
            sig_display = f" -- `{signature[:60]}`" if signature else ""
            lines.append(f"- [{item['workflow_name']} / {item['job']}]({item['issue']}){sig_display}")
        lines.append("")

    if dry_run:
        lines.append(f"## Dry Run ({len(dry_run)})\n")
        for item in dry_run:
            lines.append(f"- {item['workflow_name']} / {item['job']}")
        lines.append("")

    if skipped:
        lines.append(f"## Agent Skipped ({len(skipped)})\n")
        for item in skipped:
            lines.append(f"- {item['workflow_name']} / {item['job']}: {item.get('reason', '')}")
        lines.append("")

    if open_issues:
        lines.append(f"## All Open Tracked Issues ({len(open_issues)})\n")
        lines.append("| Workflow / Job | Issue | Signature |")
        lines.append("|----------------|-------|-----------|")
        for issue in open_issues:
            sig = _extract_signature_snippet(issue.get("body", ""))
            sig_cell = f"`{sig}`" if sig else ""
            lines.append(
                f"| {issue.get('title', '')} | [#{issue.get('number', '')}]({issue.get('url', '')}) | {sig_cell} |"
            )
        lines.append("")

    return "\n".join(lines)
