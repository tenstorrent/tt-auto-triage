"""JSON-after-marker parsing helpers for agent output."""

from __future__ import annotations

import json
import re
from typing import Any


def parse_json_after_marker(text: str, marker: str) -> dict[str, Any]:
    """Simple ``str.find`` parser (M5 / issue_lifecycle / slack_thread pattern).

    Raises on missing marker or non-object payload.
    """
    idx = text.find(marker)
    if idx < 0:
        raise ValueError(f"marker not found: {marker}")
    payload = text[idx + len(marker) :].strip()
    if not payload:
        raise ValueError("empty json payload after marker")
    obj = json.loads(payload)
    if not isinstance(obj, dict):
        raise ValueError("payload after marker is not a JSON object")
    return obj


def strip_json_fence(payload: str) -> str:
    """Remove optional triple-backtick JSON fencing."""
    stripped = payload.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
        stripped = stripped.strip()
    return stripped


def parse_agent_json_payload(text: str, *, marker: str) -> Any:
    """Robust ``rfind``-based parser with multiple fallbacks (M4 pattern).

    Tries, in order: marker extraction, raw JSON, last fenced block,
    trailing ``{`` / ``[`` scan.
    """
    idx = text.rfind(marker)
    if idx >= 0:
        payload = strip_json_fence(text[idx + len(marker) :])
        return json.loads(payload)

    stripped = text.strip()
    if not stripped:
        raise ValueError(f"marker not found: {marker}. output excerpt: <empty>")

    try:
        return json.loads(strip_json_fence(stripped))
    except Exception:
        pass

    fenced = re.findall(r"```(?:json)?\s*(.*?)```", stripped, flags=re.IGNORECASE | re.DOTALL)
    for block in reversed(fenced):
        candidate = block.strip()
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except Exception:
            continue

    for match in re.finditer(r"[\{\[]", stripped):
        candidate = stripped[match.start() :].strip()
        try:
            return json.loads(candidate)
        except Exception:
            continue

    excerpt = stripped[-600:].replace("\n", "\\n")
    raise ValueError(f"marker not found: {marker}. Could not parse fallback JSON. output excerpt: {excerpt}")


def parse_agent_json_after_marker(text: str, marker: str) -> dict[str, Any]:
    """``rfind``-based parser with regex marker matching and fence
    stripping (execute_disable pattern).
    """
    idx = text.rfind(marker)
    marker_end = idx + len(marker) if idx >= 0 else -1
    if idx < 0:
        marker_re = re.compile(rf"`?\s*{re.escape(marker)}\s*`?")
        matches = list(marker_re.finditer(text))
        if matches:
            marker_end = matches[-1].end()
    if marker_end < 0:
        raise ValueError(f"marker not found: {marker}")

    payload = text[marker_end:].strip()
    if payload.startswith("```"):
        payload = re.sub(r"^```(?:json)?\s*", "", payload)
        payload = re.sub(r"\s*```$", "", payload)
        payload = payload.strip()

    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        brace_idx = payload.find("{")
        if brace_idx < 0:
            raise
        return json.loads(payload[brace_idx:])
