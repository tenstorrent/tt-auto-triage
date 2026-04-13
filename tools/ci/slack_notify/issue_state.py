from __future__ import annotations

import json
import re
import time
from typing import Any

SLACK_SENT_LABEL = "auto-triage:slack-sent"
OWNERS_READY_LABEL = "auto-triage:owners-ready"
METADATA_START = "<!-- AUTO-TRIAGE-METADATA-START -->"
METADATA_END = "<!-- AUTO-TRIAGE-METADATA-END -->"
_SENDING_STALE_AFTER_SECONDS = 1800

_SLACK_MARKERS = (
    "Auto-triage-slack-channel",
    "Auto-triage-slack-status",
    "Auto-triage-slack-ts",
)


def _extract_metadata_block(body: str) -> str:
    match = re.search(
        rf"{re.escape(METADATA_START)}\n?(.*?)\n?{re.escape(METADATA_END)}",
        body,
        re.DOTALL,
    )
    return match.group(1).strip() if match else ""


def _remove_metadata_block(body: str) -> str:
    cleaned = re.sub(
        rf"\n?{re.escape(METADATA_START)}\n?.*?\n?{re.escape(METADATA_END)}",
        "",
        body,
        flags=re.DOTALL,
    )
    return cleaned.strip()


def _strip_marker_lines(metadata: str, marker_names: tuple[str, ...]) -> str:
    lines: list[str] = []
    for raw_line in metadata.splitlines():
        line = raw_line.strip()
        if any(line.startswith(f"`{marker}:") for marker in marker_names):
            continue
        lines.append(raw_line)
    return "\n".join(lines).strip()


def _parse_json_list(body: str, marker: str) -> list[str]:
    match = re.search(rf"{re.escape(marker)}:\s*`?([^\n`]+)`?", body)
    if not match:
        return []
    try:
        value = json.loads(match.group(1).strip())
    except json.JSONDecodeError:
        return []
    return [str(item) for item in value if str(item)]


def _parse_string(body: str, marker: str) -> str:
    match = re.search(rf"{re.escape(marker)}:\s*`?([^\n`]+)`?", body)
    if not match:
        return ""
    return match.group(1).strip()


def parse_assignee_markers(body: str) -> dict[str, object]:
    metadata = _extract_metadata_block(body)
    return {
        "github_assignees": _parse_json_list(metadata, "Auto-triage-assignees-gh"),
        "slack_assignees": _parse_json_list(metadata, "Auto-triage-assignees-slack"),
        "source": _parse_string(metadata, "Auto-triage-assignee-source"),
    }


def should_notify_issue(issue: dict[str, Any]) -> bool:
    body = issue.get("body") or ""
    labels = {label.get("name", "") for label in issue.get("labels", [])}
    assignees = parse_assignee_markers(body)
    if not assignees["github_assignees"] and not assignees["slack_assignees"]:
        return False
    if OWNERS_READY_LABEL not in labels:
        return False
    if SLACK_SENT_LABEL in labels:
        return False
    metadata = _extract_metadata_block(body)
    status = _parse_string(metadata, "Auto-triage-slack-status").lower()
    if status == "sent":
        return False
    if status == "sending":
        ts = _parse_string(metadata, "Auto-triage-slack-ts")
        if not ts:
            return True
        try:
            return time.time() - float(ts) >= _SENDING_STALE_AFTER_SECONDS
        except ValueError:
            return True
    return True


def upsert_slack_markers(body: str, *, channel: str, status: str, ts: str) -> str:
    metadata = _extract_metadata_block(body)
    preserved = _strip_marker_lines(metadata, _SLACK_MARKERS)
    metadata_parts = [preserved] if preserved else []
    metadata_parts.append(f"`Auto-triage-slack-channel: {channel}`")
    metadata_parts.append(f"`Auto-triage-slack-status: {status}`")
    if ts:
        metadata_parts.append(f"`Auto-triage-slack-ts: {ts}`")
    metadata_block = "\n".join([METADATA_START, *metadata_parts, METADATA_END])
    return "\n\n".join(
        part for part in (_remove_metadata_block(body), metadata_block) if part
    ).strip()
