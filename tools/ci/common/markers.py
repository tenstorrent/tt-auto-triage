"""JSON-after-marker parsing helpers for agent output.

Three parsers with increasing flexibility:

* ``parse_strict``  — exact marker match, JSON object only.
                      Used by M5, issue_lifecycle, slack_thread_agent_analysis.
* ``parse_with_fallbacks`` — marker + multiple fallback strategies (raw, fenced, brace scan).
                      Used by M4 batch issue creation.
* ``parse_with_regex``  — regex-tolerant marker match + fence stripping + brace fallback.
                      Used by execute_disable_actions.

Legacy aliases (``parse_json_after_marker``, ``parse_agent_json_payload``,
``parse_agent_json_after_marker``) are kept for backward compatibility.
"""

from __future__ import annotations

import json
import re
from typing import Any


def parse_strict(text: str, marker: str) -> dict[str, Any]:
    """Exact marker match, returns a single JSON object.

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


def parse_with_fallbacks(text: str, *, marker: str) -> Any:
    """Marker match with multiple fallback strategies.

    Tries, in order: marker extraction, raw JSON, last fenced block,
    last ``{`` / ``[`` scan (scanning from end for robustness).
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

    for match in reversed(list(re.finditer(r"[\{\[]", stripped))):
        candidate = stripped[match.start() :].strip()
        try:
            return json.loads(candidate)
        except Exception:
            continue

    excerpt = stripped[-600:].replace("\n", "\\n")
    raise ValueError(f"marker not found: {marker}. Could not parse fallback JSON. output excerpt: {excerpt}")


def parse_with_regex(text: str, marker: str) -> dict[str, Any]:
    """Regex-tolerant marker match with fence stripping and brace fallback."""
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


# Backward-compatible aliases
parse_json_after_marker = parse_strict
parse_agent_json_payload = parse_with_fallbacks
parse_agent_json_after_marker = parse_with_regex
