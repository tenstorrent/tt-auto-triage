#!/usr/bin/env python3
"""
Replace Slack usergroup IDs (S-prefixed) with a randomly chosen individual
member from that group, so one person gets pinged instead of the whole team.

Reads configuration from stdin as JSON:
  {
    "slack_groups": "path/to/slack_groups.json",
    "slack_directory": "path/to/slack_directory.json",
    "files": ["slack_message.json", "job_owner.json"]
  }

Each input file is modified in place. Person objects whose slack_id starts
with "S" are resolved to a random active member of that group.
"""

import json
import secrets
import sys
from pathlib import Path
from typing import Any, Dict, Optional


def load_json_file(filepath: Path) -> Any:
    if not filepath.is_file():
        return None
    return json.loads(filepath.read_text(encoding="utf-8"))


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
    active = [
        user_index[uid]
        for uid in member_ids
        if uid in user_index
        and not user_index[uid].get("is_bot")
        and not user_index[uid].get("deleted")
    ]
    if not active:
        return None
    return secrets.choice(active)


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


def process_file(filepath: Path, group_index: Dict, user_index: Dict) -> int:
    """Load a JSON file, resolve group pings, and write it back."""
    if not filepath.is_file():
        return 0

    print(f"Processing {filepath}")
    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError) as e:
        print(f"  Skipping {filepath}: invalid JSON ({e})")
        return 0

    resolved = walk_person_fields(data, group_index, user_index)

    if resolved > 0:
        filepath.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"  Resolved {resolved} group ping(s)")
    else:
        print(f"  No group pings found")

    return resolved


def main() -> int:
    try:
        config = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"Error: invalid JSON on stdin: {e}", file=sys.stderr)
        return 1

    groups_path = Path(config.get("slack_groups", ""))
    directory_path = Path(config.get("slack_directory", ""))
    file_paths = [Path(f) for f in config.get("files", [])]

    if not groups_path.name or not directory_path.name or not file_paths:
        print("Error: stdin JSON must contain slack_groups, slack_directory, and files",
              file=sys.stderr)
        return 1

    groups_data = load_json_file(groups_path)
    if groups_data is None:
        print(f"Warning: {groups_path} not found, skipping resolution", file=sys.stderr)
        return 0

    users_data = load_json_file(directory_path)
    if users_data is None:
        print(f"Warning: {directory_path} not found, skipping resolution", file=sys.stderr)
        return 0

    group_index = build_group_index(groups_data)
    user_index = build_user_index(users_data)
    print(f"Loaded {len(group_index)} groups, {len(user_index)} users")

    total = 0
    for fp in file_paths:
        total += process_file(fp, group_index, user_index)

    print(f"Total: {total} group ping(s) resolved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
