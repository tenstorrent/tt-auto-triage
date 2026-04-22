from __future__ import annotations

from typing import Any

from .helpers import api_get
from .issue_state import AUTO_TRIAGE_LABEL


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
            lines.append(f"- [{item['workflow_name']} / {item['job']}]({item['issue']})")
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
        lines.append("| Workflow / Job | Issue |")
        lines.append("|----------------|-------|")
        for issue in open_issues:
            lines.append(
                f"| {issue.get('title', '')} | [#{issue.get('number', '')}]({issue.get('url', '')}) |"
            )
        lines.append("")

    return "\n".join(lines)
