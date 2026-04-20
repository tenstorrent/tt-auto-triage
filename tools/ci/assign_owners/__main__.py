#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .github import download_slack_directory, list_open_issues, log, update_issue
from .issue_state import parse_assignee_markers, parse_base_markers, upsert_assignee_markers
from .owners import (
    build_commit_identity_index,
    load_codeowners,
    load_owners_json,
    load_pipeline_reorg_owners,
    resolve_owners,
)

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
TARGET_REPO_ROOT = Path(os.environ.get("TARGET_REPO_ROOT", "tt-metal"))
GIT_HISTORY_MAX_COMMITS = int(os.environ.get("GIT_HISTORY_MAX_COMMITS", "100"))
IDENTITY_INDEX_MAX_COMMITS = int(os.environ.get("IDENTITY_INDEX_MAX_COMMITS", "5000"))


def _format_gh(logins: list[str], names: list[str]) -> str:
    if not logins:
        return "_none_"
    parts: list[str] = []
    for i, login in enumerate(logins):
        name = names[i] if i < len(names) else ""
        parts.append(f"`{login}`" + (f" ({name})" if name else ""))
    return ", ".join(parts)


def _format_slack(ids: list[str], names: list[str]) -> str:
    if not ids:
        return "_none_"
    parts: list[str] = []
    for i, sid in enumerate(ids):
        name = names[i] if i < len(names) else ""
        parts.append((f"{name} (`{sid}`)" if name else f"`{sid}`"))
    return ", ".join(parts)


def render_summary(
    updated: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    unchanged: list[dict[str, Any]],
    failed: list[dict[str, Any]] | None = None,
) -> str:
    failed = failed or []
    lines: list[str] = ["# Assignee Resolution Summary", ""]
    lines.append(f"- **Updated:** {len(updated)}")
    lines.append(f"- **Unchanged (idempotent):** {len(unchanged)}")
    lines.append(f"- **Skipped (missing base metadata):** {len(skipped)}")
    if failed:
        lines.append(f"- **Failed:** {len(failed)}")
    lines.append("")

    if updated:
        lines.append("## Assigned owners")
        lines.append("")
        lines.append("| Issue | Source | GitHub (login + name) | Slack (name + id) |")
        lines.append("| --- | --- | --- | --- |")
        for row in updated:
            lines.append(
                f"| #{row['number']} | `{row['source']}` | "
                f"{_format_gh(row['github_assignees'], row['github_names'])} | "
                f"{_format_slack(row['slack_assignees'], row['slack_names'])} |"
            )
        lines.append("")

    if unchanged:
        lines.append("## Already up-to-date")
        lines.append("")
        for row in unchanged:
            lines.append(f"- #{row['number']} (source: `{row['source']}`)")
        lines.append("")

    if skipped:
        lines.append("## Skipped")
        lines.append("")
        for row in skipped:
            lines.append(f"- #{row['number']}: {row['reason']}")
        lines.append("")

    if failed:
        lines.append("## Failed (transient / unexpected)")
        lines.append("")
        for row in failed:
            lines.append(f"- #{row['number']}: {row['reason']}")
        lines.append("")

    if not (updated or unchanged or skipped or failed):
        lines.append("No CI auto-triage issues found in the target repo.")
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

    commit_identity_index = build_commit_identity_index(
        TARGET_REPO_ROOT, IDENTITY_INDEX_MAX_COMMITS,
    )
    log(
        f"  Built commit identity index: {len(commit_identity_index.get('by_name', {}))} names, "
        f"{len(commit_identity_index.get('by_email', {}))} emails"
    )

    updated: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for issue in issues:
        number = issue["number"]
        base = parse_base_markers(issue.get("body", ""))
        workflow_name = str(base["workflow_name"])
        job_name = str(base["job_name"])
        if not workflow_name or not job_name:
            log(f"  #{number}: missing base metadata, skipping")
            skipped.append({"number": number, "reason": "missing Auto-triage-workflow / Auto-triage-job-name"})
            continue

        try:
            resolved = _process_issue(
                issue, number, workflow_name, job_name,
                owners_json, pipeline_owners, codeowners, slack_directory,
                commit_identity_index,
                updated, unchanged,
            )
        except Exception as exc:  # noqa: BLE001
            log(f"  #{number}: failed — {exc}")
            failed.append({"number": number, "reason": str(exc)})
        else:
            del resolved  # return value unused; outcomes recorded in updated/unchanged

    summary = render_summary(updated, skipped, unchanged, failed)
    if SUMMARY_OUTPUT:
        Path(SUMMARY_OUTPUT).write_text(summary, encoding="utf-8")
    else:
        print(summary)
    print(json.dumps({"updated": updated, "unchanged": unchanged, "skipped": skipped, "failed": failed}, indent=2), file=sys.stderr)
    return 1 if failed else 0


def _process_issue(
    issue: dict[str, Any],
    number: Any,
    workflow_name: str,
    job_name: str,
    owners_json: list[dict[str, Any]],
    pipeline_owners: list[dict[str, Any]],
    codeowners: dict[str, list[str]],
    slack_directory: list[dict[str, Any]],
    commit_identity_index: dict[str, list[dict[str, str]]],
    updated: list[dict[str, Any]],
    unchanged: list[dict[str, Any]],
) -> dict[str, object]:
    resolved = resolve_owners(
        workflow_name,
        job_name,
        owners_json,
        pipeline_owners,
        codeowners,
        slack_directory,
        os.environ.get("GITHUB_TOKEN"),
        repo_root=TARGET_REPO_ROOT,
        git_history_max_commits=GIT_HISTORY_MAX_COMMITS,
        commit_identity_index=commit_identity_index,
    )
    github_assignees = list(resolved["github_assignees"])  # type: ignore[arg-type]
    github_names = list(resolved.get("github_names", []))  # type: ignore[arg-type]
    slack_assignees = list(resolved["slack_assignees"])  # type: ignore[arg-type]
    slack_names = list(resolved.get("slack_names", []))  # type: ignore[arg-type]
    source = str(resolved["source"])

    existing = parse_assignee_markers(issue.get("body", ""))
    existing_labels = {label.get("name", "") for label in issue.get("labels", [])}
    if (
        existing == {
            "github_assignees": github_assignees,
            "slack_assignees": slack_assignees,
            "source": source,
        }
        and OWNERS_READY_LABEL in existing_labels
    ):
        log(f"  #{number}: assignees unchanged, skipping update")
        unchanged.append({"number": number, "source": source})
        return resolved

    new_body = upsert_assignee_markers(
        issue.get("body", ""),
        github_assignees=github_assignees,
        slack_assignees=slack_assignees,
        source=source,
    )
    update_issue(
        ISSUE_REPO,
        number,
        new_body,
        ISSUE_WRITE_TOKEN,
        add_labels=[OWNERS_READY_LABEL],
    )
    updated.append(
        {
            "number": number,
            "source": source,
            "github_assignees": github_assignees,
            "github_names": github_names,
            "slack_assignees": slack_assignees,
            "slack_names": slack_names,
        }
    )
    log(f"  #{number}: source={source} gh={github_assignees} slack={slack_assignees}")
    return resolved


if __name__ == "__main__":
    raise SystemExit(main())
