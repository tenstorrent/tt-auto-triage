#!/usr/bin/env python3
"""Export recent Slack channel activity (including thread replies) to JSON."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.ci.common.slack import slack_api_get
from tools.ci.common.timestamps import iso_utc

DEFAULT_CHANNEL_ID = "C0APK6215B5"
DEFAULT_DAYS = 14


def fetch_channel_messages(token: str, channel_id: str, oldest_ts: float, latest_ts: float) -> list[dict[str, Any]]:
    """Fetch all top-level channel messages in the requested window."""
    messages: list[dict[str, Any]] = []
    cursor: str | None = None

    while True:
        params: dict[str, Any] = {
            "channel": channel_id,
            "limit": 200,
            "oldest": f"{oldest_ts:.6f}",
            "latest": f"{latest_ts:.6f}",
            "inclusive": "true",
        }
        if cursor:
            params["cursor"] = cursor

        payload = slack_api_get(token, "conversations.history", params)
        messages.extend(payload.get("messages", []))

        cursor = payload.get("response_metadata", {}).get("next_cursor") or None
        if not cursor:
            break

    return messages


def fetch_thread_replies(
    token: str, channel_id: str, thread_ts: str, oldest_ts: float, latest_ts: float
) -> list[dict[str, Any]]:
    """Fetch replies for a thread root within the requested window."""
    replies: list[dict[str, Any]] = []
    cursor: str | None = None

    while True:
        params: dict[str, Any] = {
            "channel": channel_id,
            "ts": thread_ts,
            "limit": 200,
            "oldest": f"{oldest_ts:.6f}",
            "latest": f"{latest_ts:.6f}",
            "inclusive": "true",
        }
        if cursor:
            params["cursor"] = cursor

        payload = slack_api_get(token, "conversations.replies", params)
        batch = payload.get("messages", [])
        replies.extend([m for m in batch if m.get("ts") != thread_ts])

        cursor = payload.get("response_metadata", {}).get("next_cursor") or None
        if not cursor:
            break

    return replies


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export last N days of Slack channel messages (including thread replies) to JSON."
    )
    parser.add_argument("--channel-id", default=DEFAULT_CHANNEL_ID, help="Slack channel ID")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="Lookback window in days")
    parser.add_argument(
        "--output",
        default=f"build_ci/raw_data/slack_{DEFAULT_CHANNEL_ID}_last_{DEFAULT_DAYS}_days.json",
        help="Output JSON file path",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.days <= 0:
        print("--days must be positive", file=sys.stderr)
        return 2

    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        print("SLACK_BOT_TOKEN is not set in this shell.", file=sys.stderr)
        return 2

    now_ts = time.time()
    oldest_ts = now_ts - (args.days * 24 * 60 * 60)

    top_level_messages = fetch_channel_messages(token, args.channel_id, oldest_ts, now_ts)
    top_level_messages.sort(key=lambda m: float(m.get("ts", "0")))

    results: list[dict[str, Any]] = []
    total_replies = 0

    for msg in top_level_messages:
        item: dict[str, Any] = {
            "ts": msg.get("ts"),
            "thread_ts": msg.get("thread_ts", msg.get("ts")),
            "user": msg.get("user"),
            "bot_id": msg.get("bot_id"),
            "subtype": msg.get("subtype"),
            "text": msg.get("text", ""),
            "reply_count": msg.get("reply_count", 0),
            "latest_reply": msg.get("latest_reply"),
            "raw": msg,
        }

        if int(msg.get("reply_count", 0)) > 0 and msg.get("ts"):
            replies = fetch_thread_replies(token, args.channel_id, msg["ts"], oldest_ts, now_ts)
            replies.sort(key=lambda r: float(r.get("ts", "0")))
            item["replies"] = replies
            total_replies += len(replies)
        else:
            item["replies"] = []

        results.append(item)

    payload = {
        "exported_at_utc": iso_utc(now_ts),
        "channel_id": args.channel_id,
        "window": {
            "days": args.days,
            "oldest_ts": f"{oldest_ts:.6f}",
            "latest_ts": f"{now_ts:.6f}",
            "oldest_utc": iso_utc(oldest_ts),
            "latest_utc": iso_utc(now_ts),
        },
        "counts": {
            "top_level_messages": len(results),
            "thread_replies": total_replies,
            "total_items": len(results) + total_replies,
        },
        "messages": results,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Wrote Slack export to {output_path}")
    print(
        "Counts:",
        json.dumps(payload["counts"], separators=(",", ":")),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
