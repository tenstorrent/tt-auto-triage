from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any


def slack_post(token: str, channel: str, text: str) -> dict[str, Any]:
    data = urllib.parse.urlencode({"channel": channel, "text": text}).encode()
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read())
    if not payload.get("ok"):
        raise RuntimeError(f"Slack error: {payload.get('error', 'unknown')}")
    return payload
