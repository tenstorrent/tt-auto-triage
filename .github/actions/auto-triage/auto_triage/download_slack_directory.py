#!/usr/bin/env python3
"""
Download Slack users and user groups into separate JSON files.

Requirements:
  * A Slack bot token with `users:read` and/or `usergroups:read`.
  * The token must be provided via the SLACK_BOT_TOKEN environment variable.

If the token lacks one of the scopes, the script still writes whichever data
could be retrieved (users or user groups).
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import requests


SLACK_API_BASE = "https://slack.com/api"
DEFAULT_USERS_OUTPUT = "auto_triage/data/slack_directory.json"
DEFAULT_GROUPS_OUTPUT = "auto_triage/data/slack_groups.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download Slack users and user groups into separate JSON files."
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_USERS_OUTPUT,
        help=f"Output file path for users (default: {DEFAULT_USERS_OUTPUT}).",
    )
    parser.add_argument(
        "--groups-output",
        default=DEFAULT_GROUPS_OUTPUT,
        help=f"Output file path for groups (default: {DEFAULT_GROUPS_OUTPUT}).",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Emit human-readable JSON (indent=2).",
    )
    return parser


def fetch_all_users(token: str) -> List[Dict]:
    users = []
    cursor = None
    params = {"limit": 200}

    while True:
        if cursor:
            params["cursor"] = cursor
        response = requests.get(
            f"{SLACK_API_BASE}/users.list",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=30,
        )
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(
                f"Slack API error while fetching users: {data.get('error', 'unknown_error')}"
            )
        users.extend(data.get("members", []))
        cursor = data.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return users


def fetch_all_usergroups(token: str) -> List[Dict]:
    response = requests.get(
        f"{SLACK_API_BASE}/usergroups.list",
        headers={"Authorization": f"Bearer {token}"},
        params={"include_users": "true"},
        timeout=30,
    )
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(
            f"Slack API error while fetching user groups: {data.get('error', 'unknown_error')}"
        )
    return data.get("usergroups", [])


def serialize_users(raw_users: List[Dict]) -> List[Dict]:
    serialized = []
    for user in raw_users:
        profile = user.get("profile", {})
        serialized.append(
            {
                "id": user.get("id"),
                "display_name": profile.get("display_name"),
                "real_name": user.get("real_name"),
                "username": user.get("name"),
                "email": profile.get("email"),
                "is_bot": user.get("is_bot", False),
                "deleted": user.get("deleted", False),
            }
        )
    return serialized


def serialize_usergroups(raw_groups: List[Dict]) -> List[Dict]:
    serialized = []
    for group in raw_groups:
        serialized.append(
            {
                "id": group.get("id"),
                "handle": group.get("handle"),
                "name": group.get("name"),
                "description": group.get("description"),
                "users": group.get("users", []),
            }
        )
    return serialized


def write_output(path: str, payload: Dict, pretty: bool) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        if pretty:
            json.dump(payload, fh, indent=2, sort_keys=True)
        else:
            json.dump(payload, fh, separators=(",", ":"))


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        print("Error: SLACK_BOT_TOKEN is not set.", file=sys.stderr)
        return 1

    users_data: List[Dict] = []
    usergroups_data: List[Dict] = []
    fetched_any = False

    try:
        users_data = serialize_users(fetch_all_users(token))
        fetched_any = fetched_any or bool(users_data)
        print(f"Fetched {len(users_data)} users.")
    except Exception as exc:
        print(f"Warning: Failed to fetch users ({exc}).", file=sys.stderr)

    try:
        usergroups_data = serialize_usergroups(fetch_all_usergroups(token))
        fetched_any = fetched_any or bool(usergroups_data)
        print(f"Fetched {len(usergroups_data)} user groups.")
    except Exception as exc:
        print(f"Warning: Failed to fetch user groups ({exc}).", file=sys.stderr)

    if not fetched_any:
        print(
            "Error: Unable to fetch users or user groups with the provided token.",
            file=sys.stderr,
        )
        return 2

    # Write users to separate file
    if users_data:
        users_payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "users": users_data,
        }
        write_output(args.output, users_payload, args.pretty)
        print(f"Wrote Slack users directory to {args.output}")
    else:
        print("Warning: No users fetched, skipping users file.", file=sys.stderr)

    # Write groups to separate file
    if usergroups_data:
        groups_payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "usergroups": usergroups_data,
        }
        write_output(args.groups_output, groups_payload, args.pretty)
        print(f"Wrote Slack groups directory to {args.groups_output}")
    else:
        print("Warning: No groups fetched, skipping groups file.", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


