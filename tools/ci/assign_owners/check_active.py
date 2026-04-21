#!/usr/bin/env python3
"""Employment check CLI for the owner-resolution agent fallback.

Given a Slack ID and/or GitHub login, print `ACTIVE` or `INACTIVE: <reason>`
and exit 0 (always), so the agent can check the result by parsing stdout.
The reasons mirror `is_active_employee` in `__main__.py`:
  * appears in EX_EMPLOYEES env var (escape hatch for emergencies)
  * Slack user flagged `deleted: true`
  * Slack user completely absent from a non-empty Slack dump

The agent uses this instead of parsing the Slack dump itself, so the
definition of "active" is centralized and the agent can't drift from the
deterministic fast-path's validation.

Usage:
    python3 -m tools.ci.assign_owners.check_active \
        --slack-dump slack_users.json \
        --slack-id U05RWH3QUPM
    python3 -m tools.ci.assign_owners.check_active \
        --slack-dump slack_users.json \
        --login sadesoyeTT
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


def _load_ex_employees() -> frozenset[str]:
    raw = os.environ.get("EX_EMPLOYEES", "")
    return frozenset(v.strip() for v in re.split(r"[\s,]+", raw) if v.strip())


def _load_slack_dump(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def check(slack_id: str, login: str, slack_dir: list[dict[str, Any]], ex: frozenset[str]) -> tuple[bool, str]:
    """Return (is_active, reason_if_inactive). Kept deliberately aligned with
    `__main__.is_active_employee` so the agent and the fast path apply the
    same definition of "active employee"."""
    if slack_id and slack_id in ex:
        return False, "ex-employees override (slack_id)"
    if login and login in ex:
        return False, "ex-employees override (github login)"
    if slack_id and slack_dir:
        user = next((u for u in slack_dir if u.get("id") == slack_id), None)
        if user is None:
            return False, "not present in Slack workspace dump"
        if user.get("deleted"):
            return False, "Slack account deactivated"
    return True, ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check whether a candidate owner is an active employee.")
    parser.add_argument("--slack-id", default="", help="Slack user ID (e.g. U05RWH3QUPM)")
    parser.add_argument("--login", default="", help="GitHub login (e.g. sadesoyeTT)")
    parser.add_argument("--slack-dump", default=os.environ.get("SLACK_DUMP_PATH", "slack_users.json"),
                        help="Path to the downloaded Slack users dump")
    args = parser.parse_args(argv)

    if not (args.slack_id or args.login):
        print("ERROR: supply at least one of --slack-id or --login", file=sys.stderr)
        return 2

    slack_dir = _load_slack_dump(Path(args.slack_dump))
    active, reason = check(args.slack_id, args.login, slack_dir, _load_ex_employees())
    if active:
        print("ACTIVE")
    else:
        print(f"INACTIVE: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
