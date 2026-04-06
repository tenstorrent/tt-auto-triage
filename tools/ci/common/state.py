"""Triage state persistence helpers."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .timestamps import now_utc

log = logging.getLogger(__name__)

SUPPORTED_VERSIONS = {1}

ALLOWED_STATUS = {
    "new",
    "planned",
    "pr_open",
    "kickoff_running",
    "kickoff_failed_new_failure",
    "completed",
    "needs_human",
    "paused",
}


def empty_state() -> dict[str, Any]:
    return {"version": 1, "updated_at_utc": now_utc(), "items": []}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at_utc"] = now_utc()
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def load_state(path: Path, *, schema: str = "base") -> dict[str, Any]:
    """Load triage state from *path*.

    *schema* controls validation strictness:

    - ``"base"`` -- minimal (M5 pattern): missing file returns ``empty_state()``.
    - ``"issue_lifecycle"`` -- ensures nested ``issue_lifecycle.issues`` dict.
    - ``"disable"`` -- strict validation of keys, statuses, and attempts.
    """
    if schema == "base":
        return _load_base(path)
    if schema == "issue_lifecycle":
        return _load_issue_lifecycle(path)
    if schema == "disable":
        return _load_disable(path)
    raise ValueError(f"unknown state schema: {schema}")


def _load_base(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_state()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return empty_state()
    _warn_unsupported_version(payload)
    return payload


def _load_issue_lifecycle(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "updated_at_utc": now_utc(), "items": [], "issue_lifecycle": {"issues": {}}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        payload = {"version": 1, "updated_at_utc": now_utc(), "items": []}
    _warn_unsupported_version(payload)
    issue_lifecycle = payload.get("issue_lifecycle")
    if not isinstance(issue_lifecycle, dict):
        issue_lifecycle = {}
    issues = issue_lifecycle.get("issues")
    if not isinstance(issues, dict):
        issues = {}
    issue_lifecycle["issues"] = issues
    payload["issue_lifecycle"] = issue_lifecycle
    return payload


def _load_disable(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_state()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("state must be a JSON object")
    if not isinstance(data.get("items"), list):
        raise ValueError("state.items must be a list")
    _warn_unsupported_version(data)
    seen: set[str] = set()
    for item in data["items"]:
        if not isinstance(item, dict):
            raise ValueError("state items must be objects")
        key = item.get("key")
        if not isinstance(key, str) or not key:
            raise ValueError(f"state item has missing or non-string key: {key!r}")
        if key in seen:
            raise ValueError(f"duplicate state key: {key}")
        seen.add(key)
        status = item.get("status")
        if not isinstance(status, str) or status not in ALLOWED_STATUS:
            raise ValueError(f"invalid state status for {key}: {status!r}")
        attempts = item.get("attempts", 0)
        if not isinstance(attempts, int) or attempts < 0:
            raise ValueError(f"invalid attempts for {key}")
    return data


def append_history(item: dict[str, Any], event: str, details: str) -> None:
    """Append a timestamped event to an item's history list."""
    history = item.setdefault("history", [])
    if not isinstance(history, list):
        log.warning("item['history'] was %s, resetting to list", type(history).__name__)
        item["history"] = []
        history = item["history"]
    history.append({"ts_utc": now_utc(), "event": event, "details": details})


def _warn_unsupported_version(data: dict[str, Any]) -> None:
    version = data.get("version")
    if version not in SUPPORTED_VERSIONS:
        log.warning("state file version %r not in %s -- loading anyway", version, SUPPORTED_VERSIONS)
