from __future__ import annotations

import json
import re

METADATA_START = "<!-- AUTO-TRIAGE-METADATA-START -->"
METADATA_END = "<!-- AUTO-TRIAGE-METADATA-END -->"

_ASSIGNEE_MARKERS = (
    "Auto-triage-assignees-gh",
    "Auto-triage-assignees-slack",
    "Auto-triage-assignee-source",
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


def parse_base_markers(body: str) -> dict[str, object]:
    metadata = _extract_metadata_block(body)
    return {
        "workflow_name": _parse_string(metadata, "Auto-triage-workflow"),
        "job_name": _parse_string(metadata, "Auto-triage-job-name"),
        "fingerprint": _parse_string(metadata, "Auto-triage-fingerprint"),
    }


def parse_assignee_markers(body: str) -> dict[str, object]:
    metadata = _extract_metadata_block(body)
    return {
        "github_assignees": _parse_json_list(metadata, "Auto-triage-assignees-gh"),
        "slack_assignees": _parse_json_list(metadata, "Auto-triage-assignees-slack"),
        "source": _parse_string(metadata, "Auto-triage-assignee-source"),
    }


def upsert_assignee_markers(
    body: str,
    *,
    github_assignees: list[str],
    slack_assignees: list[str],
    source: str,
) -> str:
    metadata = _extract_metadata_block(body)
    preserved = _strip_marker_lines(metadata, _ASSIGNEE_MARKERS)
    metadata_parts = [preserved] if preserved else []
    metadata_parts.append(
        f"`Auto-triage-assignees-gh: {json.dumps(github_assignees, separators=(',', ':'))}`"
    )
    metadata_parts.append(
        f"`Auto-triage-assignees-slack: {json.dumps(slack_assignees, separators=(',', ':'))}`"
    )
    metadata_parts.append(f"`Auto-triage-assignee-source: {source}`")
    metadata_block = "\n".join([METADATA_START, *metadata_parts, METADATA_END])
    return "\n\n".join(
        part for part in (_remove_metadata_block(body), metadata_block) if part
    ).strip()
