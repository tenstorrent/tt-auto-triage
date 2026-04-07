"""Shared helpers for the auto-issue pipeline."""

from __future__ import annotations

import hashlib
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
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
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


def download_slack_directory(token: str) -> list[dict[str, Any]]:
    """Download the full Slack user directory via users.list (paginated).

    Returns a list of simplified user dicts with id, display_name,
    real_name, username, and email fields.
    """
    users: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params: dict[str, str] = {"limit": "200"}
        if cursor:
            params["cursor"] = cursor
        qs = urllib.parse.urlencode(params)
        req = urllib.request.Request(
            f"https://slack.com/api/users.list?{qs}",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        if not data.get("ok"):
            raise RuntimeError(f"Slack users.list failed: {data.get('error', 'unknown')}")
        for member in data.get("members", []):
            profile = member.get("profile", {})
            users.append({
                "id": member.get("id", ""),
                "display_name": profile.get("display_name", ""),
                "real_name": member.get("real_name", ""),
                "username": member.get("name", ""),
                "email": profile.get("email", ""),
                "is_bot": member.get("is_bot", False),
                "deleted": member.get("deleted", False),
            })
        cursor = data.get("response_metadata", {}).get("next_cursor", "")
        if not cursor:
            break
    return users


def job_identity_key(workflow_name: str, job_name: str) -> str:
    raw = f"{workflow_name}\n{job_name}".strip().lower().encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def fingerprint_for(workflow_name: str, job_name: str, signature: str) -> str:
    raw = f"{workflow_name}\n{job_name}\n{signature.strip().lower()}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]
