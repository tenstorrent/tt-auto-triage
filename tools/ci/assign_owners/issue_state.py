from __future__ import annotations

import json
import re

METADATA_START = "<!-- AUTO-TRIAGE-METADATA-START -->"
METADATA_END = "<!-- AUTO-TRIAGE-METADATA-END -->"
_ASSIGNEE_MARKERS = ("Auto-triage-assignees-gh", "Auto-triage-assignees-slack", "Auto-triage-assignee-source")
_BLOCK_RE = re.compile(rf"\n?{re.escape(METADATA_START)}\n?(.*?)\n?{re.escape(METADATA_END)}", re.DOTALL)


def _extract_metadata_block(body: str) -> str:
    m = _BLOCK_RE.search(body)
    return m.group(1).strip() if m else ""


def _remove_metadata_block(body: str) -> str:
    return _BLOCK_RE.sub("", body).strip()


def _strip_marker_lines(metadata: str, marker_names: tuple[str, ...]) -> str:
    out: list[str] = []
    for raw in metadata.splitlines():
        if any(raw.strip().startswith(f"`{m}:") for m in marker_names):
            continue
        out.append(raw)
    return "\n".join(out).strip()


def _marker_value(body: str, marker: str) -> str:
    m = re.search(rf"{re.escape(marker)}:\s*`?([^\n`]+)`?", body)
    return m.group(1).strip() if m else ""


def _parse_json_list(body: str, marker: str) -> list[str]:
    raw = _marker_value(body, marker)
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in value if str(item)]


def parse_base_markers(body: str) -> dict[str, object]:
    meta = _extract_metadata_block(body)
    return {
        "workflow_name": _marker_value(meta, "Auto-triage-workflow"),
        "job_name": _marker_value(meta, "Auto-triage-job-name"),
        "fingerprint": _marker_value(meta, "Auto-triage-fingerprint"),
    }


def parse_assignee_markers(body: str) -> dict[str, object]:
    meta = _extract_metadata_block(body)
    return {
        "github_assignees": _parse_json_list(meta, "Auto-triage-assignees-gh"),
        "slack_assignees": _parse_json_list(meta, "Auto-triage-assignees-slack"),
        "source": _marker_value(meta, "Auto-triage-assignee-source"),
    }


def upsert_assignee_markers(body: str, *, github_assignees: list[str], slack_assignees: list[str], source: str) -> str:
    preserved = _strip_marker_lines(_extract_metadata_block(body), _ASSIGNEE_MARKERS)
    parts = ([preserved] if preserved else []) + [
        f"`Auto-triage-assignees-gh: {json.dumps(github_assignees, separators=(',', ':'))}`",
        f"`Auto-triage-assignees-slack: {json.dumps(slack_assignees, separators=(',', ':'))}`",
        f"`Auto-triage-assignee-source: {source}`",
    ]
    block = "\n".join([METADATA_START, *parts, METADATA_END])
    return "\n\n".join(p for p in (_remove_metadata_block(body), block) if p).strip()
