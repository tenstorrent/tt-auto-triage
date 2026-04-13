#!/usr/bin/env python3
"""
Replace Slack usergroup IDs (S-prefixed) with a randomly chosen individual
member from that group, so one person gets pinged instead of the whole team.

Usage:
  python3 resolve_group_pings.py \
    --slack-groups path/to/slack_groups.json \
    --slack-directory path/to/slack_directory.json \
    --files slack_message.json [job_owner.json ...]

Each input file is modified in place. Person objects whose slack_id starts
with "S" are resolved to a random active member of that group.
"""

import argparse
import json
import os
import random
import sys
from typing import Any, Dict, List, Optional


def load_json(path: str) -> Any:
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        return json.load(f)


def build_group_index(groups_data: Any) -> Dict[str, Dict]:
    """Map group S-ID -> group dict (with 'users' member list)."""
    if not groups_data:
        return {}
    groups = groups_data.get("usergroups", []) if isinstance(groups_data, dict) else groups_data
    return {g["id"]: g for g in groups if isinstance(g, dict) and g.get("id")}


def build_user_index(users_data: Any) -> Dict[str, Dict]:
    """Map user U-ID -> user dict."""
    if not users_data:
        return {}
    users = users_data.get("users", []) if isinstance(users_data, dict) else users_data
    return {u["id"]: u for u in users if isinstance(u, dict) and u.get("id")}


def pick_active_member(
    group: Dict, user_index: Dict[str, Dict]
) -> Optional[Dict]:
    """Pick one random non-bot, non-deleted member from the group."""
    member_ids = group.get("users", [])
    if not member_ids:
        return None
    active = []
    for uid in member_ids:
        user = user_index.get(uid)
        if user and not user.get("is_bot") and not user.get("deleted"):
            active.append(user)
    if not active:
        return None
    return random.choice(active)


def user_display_name(user: Dict) -> str:
    return user.get("real_name") or user.get("display_name") or user.get("username") or "Unknown"


def resolve_person(person: Dict, group_index: Dict, user_index: Dict) -> bool:
    """Resolve an S-prefixed person in place. Returns True if resolved."""
    sid = person.get("slack_id", "")
    if not sid or not sid.startswith("S"):
        return False

    group = group_index.get(sid)
    if not group:
        print(f"  Group {sid} not found in slack_groups.json, leaving as plain name")
        return False

    group_name = group.get("name") or group.get("handle") or sid
    member = pick_active_member(group, user_index)
    if not member:
        print(f"  Group '{group_name}' ({sid}) has no active members, leaving as plain name")
        return False

    chosen_name = user_display_name(member)
    person["slack_id"] = member["id"]
    person["name"] = f"{chosen_name} (representing {group_name})"
    print(f"  Resolved group '{group_name}' -> {chosen_name} ({member['id']})")
    return True


def walk_person_fields(obj: Any, group_index: Dict, user_index: Dict) -> int:
    """Recursively walk a JSON structure and resolve all person objects."""
    resolved = 0
    if isinstance(obj, dict):
        if "slack_id" in obj and "name" in obj:
            if resolve_person(obj, group_index, user_index):
                resolved += 1
        for value in obj.values():
            resolved += walk_person_fields(value, group_index, user_index)
    elif isinstance(obj, list):
        for item in obj:
            resolved += walk_person_fields(item, group_index, user_index)
    return resolved


def process_file(
    path: str, group_index: Dict, user_index: Dict
) -> int:
    """Load a JSON file, resolve group pings, and write it back."""
    if not os.path.isfile(path):
        return 0

    print(f"Processing {path}")
    with open(path) as f:
        try:
            data = json.load(f)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"  Skipping {path}: invalid JSON ({e})")
            return 0

    resolved = walk_person_fields(data, group_index, user_index)

    if resolved > 0:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"  Resolved {resolved} group ping(s)")
    else:
        print(f"  No group pings found")

    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replace Slack group IDs with a random individual member."
    )
    parser.add_argument(
        "--slack-groups",
        required=True,
        help="Path to slack_groups.json (with member lists).",
    )
    parser.add_argument(
        "--slack-directory",
        required=True,
        help="Path to slack_directory.json (user data).",
    )
    parser.add_argument(
        "--files",
        nargs="+",
        required=True,
        help="JSON files to process (modified in place).",
    )
    args = parser.parse_args()

    groups_data = load_json(args.slack_groups)
    if groups_data is None:
        print(f"Warning: {args.slack_groups} not found, skipping resolution", file=sys.stderr)
        return 0

    users_data = load_json(args.slack_directory)
    if users_data is None:
        print(f"Warning: {args.slack_directory} not found, skipping resolution", file=sys.stderr)
        return 0

    group_index = build_group_index(groups_data)
    user_index = build_user_index(users_data)
    print(f"Loaded {len(group_index)} groups, {len(user_index)} users")

    total = 0
    for path in args.files:
        total += process_file(path, group_index, user_index)

    print(f"Total: {total} group ping(s) resolved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
