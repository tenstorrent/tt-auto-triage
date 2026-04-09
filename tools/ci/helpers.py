"""Shared helpers for the issue-lifecycle pipeline."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def gh(*args: str, token: str | None = None, timeout: int = 30) -> str:
    # Token injected via env rather than --auth to keep it out of process listings.
    env = {**os.environ}
    if token:
        env["GITHUB_TOKEN"] = token
    proc = subprocess.run(
        ["gh", *args],
        capture_output=True, text=True, timeout=timeout, env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:4])}... failed: {proc.stderr.strip()}")
    return proc.stdout


def api_get(url: str, token: str | None = None, retries: int = 3) -> Any:
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            if attempt < retries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            raise RuntimeError(f"API request failed: {url}: {exc}") from exc
    raise RuntimeError(f"Exhausted retries for {url}")


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
