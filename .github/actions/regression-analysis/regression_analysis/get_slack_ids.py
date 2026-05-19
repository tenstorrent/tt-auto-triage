#!/usr/bin/env python3
"""
Resolve Slack IDs by searching a pre-downloaded directory JSON.

Usage:
  ./regression_analysis/get_slack_ids.py "Alice Smith" --directory regression_analysis/data/slack_directory.json

Generate the directory with download_slack_directory.py.

Optional flags:
  --limit N        Return up to N matches per query (default: 1; 0 = all).
  --include-bots   Include bot/service accounts in results.
  --json           Emit machine-readable JSON instead of a table.
  --directory PATH Path to the JSON directory (default: regression_analysis/data/slack_directory.json).
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


DEFAULT_DIRECTORY = "regression_analysis/data/slack_directory.json"


def normalize(value: str) -> str:
    """Normalize a string for fuzzy comparisons."""
    return re.sub(r"[^a-z0-9]", "", value.lower())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve Slack user IDs from a local directory (groups are handled separately)."
    )
    parser.add_argument(
        "query",
        nargs="+",
        help="Developer names, GitHub handles, or email prefixes to search for.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1,
        help="Maximum matches to return per query (default: 1).",
    )
    parser.add_argument(
        "--include-bots",
        action="store_true",
        help="Include bot/service accounts in search results.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON output (one array entry per query).",
    )
    parser.add_argument(
        "--directory",
        default=DEFAULT_DIRECTORY,
        help=f"Path to Slack directory JSON (default: {DEFAULT_DIRECTORY}).",
    )
    return parser


def score_candidates(
    query_norm: str, candidates: Iterable[Tuple[str, str]]
) -> Tuple[int, str]:
    best_score = 0
    best_reason = ""

    for label, value in candidates:
        if not value:
            continue
        norm_value = normalize(value)
        if not norm_value:
            continue
        if norm_value == query_norm:
            score = 100
            reason = f"exact match on {label}"
        elif query_norm in norm_value:
            score = 70
            reason = f"substring match on {label}"
        else:
            continue

        if score > best_score:
            best_score = score
            best_reason = reason

    return best_score, best_reason


def score_user(query_norm: str, user: Dict) -> Tuple[int, str]:
    """Return a (score, reason) tuple for how well a user matches the query."""
    candidates = [
        ("display name", user.get("display_name")),
        ("real name", user.get("real_name")),
        ("username", user.get("username")),
        ("email", user.get("email")),
    ]
    return score_candidates(query_norm, candidates)


def score_usergroup(query_norm: str, group: Dict) -> Tuple[int, str]:
    candidates = [
        ("name", group.get("name")),
        ("handle", group.get("handle")),
        ("description", group.get("description")),
    ]
    return score_candidates(query_norm, candidates)


def search_users(query: str, users: List[Dict], include_bots: bool) -> List[Dict]:
    """Return matching users for query."""
    query_norm = normalize(query)
    results = []

    for user in users:
        if user.get("deleted"):
            continue
        if (user.get("is_bot") or user.get("id") == "USLACKBOT") and not include_bots:
            continue
        score, reason = score_user(query_norm, user)
        if score <= 0:
            continue
        results.append(
            {
                "entity_type": "user",
                "query": query,
                "id": user.get("id"),
                "display_name": user.get("display_name"),
                "real_name": user.get("real_name"),
                "email": user.get("email"),
                "reason": reason,
                "score": score,
            }
        )

    return results


def search_usergroups(query: str, usergroups: List[Dict]) -> List[Dict]:
    """Return matching user groups for query."""
    query_norm = normalize(query)
    results = []

    for group in usergroups:
        score, reason = score_usergroup(query_norm, group)
        if score <= 0:
            continue
        results.append(
            {
                "entity_type": "usergroup",
                "query": query,
                "id": group.get("id"),
                "handle": group.get("handle"),
                "name": group.get("name"),
                "description": group.get("description"),
                "reason": reason,
                "score": score,
            }
        )

    return results


def gather_matches(
    query: str,
    users: List[Dict],
    include_bots: bool,
    limit: int,
) -> List[Dict]:
    """Gather user matches only (groups are handled separately)."""
    matches = search_users(query, users, include_bots)
    matches.sort(key=lambda item: item["score"], reverse=True)
    if limit > 0:
        return matches[:limit]
    return matches


def emit_table(rows: List[List[Dict]]):
    """Emit table output for user matches only."""
    for group in rows:
        if not group:
            print("No matches found.")
            print("-" * 60)
            continue
        query = group[0]["query"]
        print(f"Query: {query}")
        print("-" * 60)
        for match in group:
            print(
                f"{match['id']:<12}  {match.get('real_name') or match.get('display_name') or '(unknown)'}"
            )
            if match.get("display_name"):
                print(f"  Display: {match['display_name']}")
            if match.get("email"):
                print(f"  Email:   {match['email']}")
            print(f"  Reason:  {match['reason']} (score {match['score']})")
            print("")
        print("-" * 60)


def load_directory(path: str) -> Dict:
    """Load users directory (groups are handled separately)."""
    directory_path = Path(path)
    if not directory_path.exists():
        raise FileNotFoundError(f"Directory file '{path}' does not exist.")
    with directory_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("users", [])
    return data


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        directory = load_directory(args.directory)
    except Exception as exc:  # pragma: no cover - CLI utility
        print(f"Failed to read Slack directory: {exc}", file=sys.stderr)
        return 1

    users = directory.get("users", [])

    all_results = []
    for query in args.query:
        matches = gather_matches(
            query, users, args.include_bots, args.limit
        )
        all_results.append(matches)

    if args.json:
        print(json.dumps(all_results, indent=2))
    else:
        emit_table(all_results)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

