#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .github import download_slack_directory, list_open_issues, log, update_issue
from .issue_state import parse_assignee_markers, parse_base_markers, upsert_assignee_markers
from .owners import load_codeowners, load_owners_json, load_pipeline_reorg_owners, resolve_owners

ISSUE_REPO = os.environ.get("ISSUE_REPO", "ebanerjeeTT/issue_dump")
TARGET_REPO = os.environ.get("TARGET_REPO", "tenstorrent/tt-metal")
ISSUE_WRITE_TOKEN = os.environ.get("ISSUE_WRITE_TOKEN", "")
OWNERS_JSON_PATH = Path(
    os.environ.get("OWNERS_JSON_PATH", "tt-metal/.github/actions/analyze-workflow-data/owners.json")
)
PIPELINE_REORG_DIR = Path(os.environ.get("PIPELINE_REORG_DIR", "tt-metal/tests/pipeline_reorg"))
CODEOWNERS_PATH = Path(os.environ.get("CODEOWNERS_PATH", "tt-metal/.github/CODEOWNERS"))
SUMMARY_OUTPUT = os.environ.get("SUMMARY_OUTPUT", "")
OWNERS_READY_LABEL = "auto-triage:owners-ready"


def render_summary(rows: list[dict[str, Any]]) -> str:
    lines = ["# Assignee Resolution Summary\n"]
    if not rows:
        lines.append("No issues were updated.\n")
        return "\n".join(lines)
    for row in rows:
        github_users = ", ".join(row["github_assignees"]) or "none"
        slack_ids = ", ".join(row["slack_assignees"]) or "none"
        lines.append(
            f"- #{row['number']}: source={row['source']} gh=[{github_users}] slack=[{slack_ids}]"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    log("=== Assign Owners ===")
    issues = list_open_issues(ISSUE_REPO, ISSUE_WRITE_TOKEN)
    owners_json = load_owners_json(OWNERS_JSON_PATH)
    pipeline_owners = load_pipeline_reorg_owners(PIPELINE_REORG_DIR)
    codeowners = load_codeowners(CODEOWNERS_PATH)

    slack_directory: list[dict[str, Any]] = []
    if os.environ.get("SLACK_BOT_TOKEN"):
        try:
            slack_directory = download_slack_directory(os.environ["SLACK_BOT_TOKEN"])
        except Exception as exc:
            log(f"  Warning: failed to download Slack directory: {exc}")

    updated: list[dict[str, Any]] = []
    for issue in issues:
        base = parse_base_markers(issue.get("body", ""))
        workflow_name = str(base["workflow_name"])
        job_name = str(base["job_name"])
        if not workflow_name or not job_name:
            continue

        agent_suggested = base.get("suggested_owners") or []
        resolved = resolve_owners(
            workflow_name,
            job_name,
            owners_json,
            pipeline_owners,
            codeowners,
            slack_directory,
            os.environ.get("GITHUB_TOKEN"),
            agent_suggested=list(agent_suggested),
        )
        github_assignees = resolved["github_assignees"]
        slack_assignees = resolved["slack_assignees"]
        source = str(resolved["source"])
        new_body = upsert_assignee_markers(
            issue.get("body", ""),
            github_assignees=list(github_assignees),
            slack_assignees=list(slack_assignees),
            source=source,
        )
        existing = parse_assignee_markers(issue.get("body", ""))
        existing_labels = {label.get("name", "") for label in issue.get("labels", [])}
        if (
            existing == {
                "github_assignees": list(github_assignees),
                "slack_assignees": list(slack_assignees),
                "source": source,
            }
            and OWNERS_READY_LABEL in existing_labels
        ):
            continue

        update_issue(
            ISSUE_REPO,
            issue["number"],
            new_body,
            ISSUE_WRITE_TOKEN,
            add_labels=[OWNERS_READY_LABEL],
        )
        updated.append(
            {
                "number": issue["number"],
                "source": source,
                "github_assignees": list(github_assignees),
                "slack_assignees": list(slack_assignees),
            }
        )

    summary = render_summary(updated)
    if SUMMARY_OUTPUT:
        Path(SUMMARY_OUTPUT).write_text(summary, encoding="utf-8")
    else:
        print(summary)
    print(json.dumps({"updated": updated}, indent=2), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
