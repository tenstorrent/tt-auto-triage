#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .github import list_open_issues, log, update_issue
from .issue_state import SLACK_SENT_LABEL, parse_assignee_markers, should_notify_issue, upsert_slack_markers
from .slack import slack_post

ISSUE_REPO = os.environ.get("ISSUE_REPO", "ebanerjeeTT/issue_dump")
ISSUE_WRITE_TOKEN = os.environ.get("ISSUE_WRITE_TOKEN", "")
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "C0APK6215B5")
SUMMARY_OUTPUT = os.environ.get("SUMMARY_OUTPUT", "")


def format_owner_text(markers: dict[str, object]) -> str:
    slack_assignees = [f"<@{value}>" for value in markers["slack_assignees"]]
    github_assignees = [f"`@{value}`" for value in markers["github_assignees"]]
    owners = slack_assignees + github_assignees
    return ", ".join(owners) if owners else "No assignees available"


def build_message(issue: dict[str, Any], markers: dict[str, object]) -> str:
    return (
        ":rotating_light: *CI auto-triage issue ready for testing triage*\n"
        f"*Issue:* <{issue['url']}|#{issue['number']} {issue['title']}>\n"
        f"*Likely owners:* {format_owner_text(markers)}\n"
        f"*Owner source:* {markers['source'] or 'none'}"
    )


def render_summary(rows: list[dict[str, Any]]) -> str:
    lines = ["# Slack Notification Summary\n"]
    if not rows:
        lines.append("No notifications were sent.\n")
        return "\n".join(lines)
    for row in rows:
        lines.append(f"- #{row['number']}: status={row['status']} ts={row['ts'] or 'n/a'}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    log("=== Slack Notify ===")
    issues = list_open_issues(ISSUE_REPO, ISSUE_WRITE_TOKEN)
    if not SLACK_BOT_TOKEN:
        log("Slack token not configured; exiting without sending.")
        issues_to_send = []
    else:
        issues_to_send = [issue for issue in issues if should_notify_issue(issue)]

    sent_rows: list[dict[str, Any]] = []
    for issue in issues_to_send:
        markers = parse_assignee_markers(issue.get("body", ""))
        sending_body = upsert_slack_markers(
            issue.get("body", ""),
            channel=SLACK_CHANNEL_ID,
            status="sending",
            ts=str(time.time()),
        )
        update_issue(
            ISSUE_REPO,
            issue["number"],
            sending_body,
            ISSUE_WRITE_TOKEN,
            add_labels=[],
        )
        message = build_message(issue, markers)
        payload = slack_post(SLACK_BOT_TOKEN, SLACK_CHANNEL_ID, message)
        ts = str(payload.get("ts", ""))
        new_body = upsert_slack_markers(
            sending_body,
            channel=SLACK_CHANNEL_ID,
            status="sent",
            ts=ts,
        )
        update_issue(
            ISSUE_REPO,
            issue["number"],
            new_body,
            ISSUE_WRITE_TOKEN,
            add_labels=[SLACK_SENT_LABEL],
        )
        sent_rows.append({"number": issue["number"], "status": "sent", "ts": ts})

    summary = render_summary(sent_rows)
    if SUMMARY_OUTPUT:
        Path(SUMMARY_OUTPUT).write_text(summary, encoding="utf-8")
    else:
        print(summary)
    print(json.dumps({"sent": sent_rows}, indent=2), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
