"""Shared Slack Web API helpers."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API_BASE = "https://slack.com/api"


def slack_api_form(token: str, endpoint: str, fields: dict[str, str]) -> dict[str, Any]:
    """POST an ``application/x-www-form-urlencoded`` request to the Slack Web API."""
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}/{endpoint}",
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def slack_api_get_simple(token: str, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
    """Simple GET against the Slack Web API (no retry / rate-limit handling)."""
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f"{API_BASE}/{endpoint}?{query}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def slack_api_get(
    token: str, endpoint: str, params: dict[str, Any], *, max_retries: int = 5,
) -> dict[str, Any]:
    """GET with automatic retry on HTTP 429 and Slack-level rate limiting."""
    url = f"{API_BASE}/{endpoint}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})

    attempt = 0
    while True:
        attempt += 1
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt <= max_retries:
                retry_after = int(exc.headers.get("Retry-After", "1"))
                time.sleep(retry_after)
                continue
            raise RuntimeError(f"HTTP error from Slack API ({endpoint}): {exc}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Network error calling Slack API ({endpoint}): {exc}") from exc

        if payload.get("ok", False):
            return payload

        error = payload.get("error", "unknown_error")
        if error == "ratelimited" and attempt <= max_retries:
            time.sleep(min(2**attempt, 30))
            continue
        raise RuntimeError(f"Slack API error from {endpoint}: {error}")


def post_slack_message(
    *, slack_token: str, channel_id: str, text: str, thread_ts: str | None = None,
) -> str:
    """Post a message (optionally in a thread) and return the message ``ts``."""
    fields: dict[str, str] = {"channel": channel_id, "text": text}
    if thread_ts:
        fields["thread_ts"] = thread_ts
    payload = slack_api_form(slack_token, "chat.postMessage", fields)
    if not payload.get("ok"):
        raise RuntimeError(f"chat.postMessage failed: {payload.get('error', 'unknown_error')}")
    return str(payload.get("ts", "")).strip()
