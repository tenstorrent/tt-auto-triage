from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
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
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:4])}... failed: {proc.stderr.strip()}")
    return proc.stdout


def api_get(url: str, token: str | None = None, retries: int = 3) -> Any:
    for attempt in range(retries):
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
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


def download_slack_directory(token: str) -> list[dict[str, Any]]:
    users: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params: dict[str, str] = {"limit": "200"}
        if cursor:
            params["cursor"] = cursor
        query = urllib.parse.urlencode(params)
        req = urllib.request.Request(
            f"https://slack.com/api/users.list?{query}",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        if not data.get("ok"):
            raise RuntimeError(f"Slack users.list failed: {data.get('error', 'unknown')}")
        for member in data.get("members", []):
            profile = member.get("profile", {})
            users.append(
                {
                    "id": member.get("id", ""),
                    "display_name": profile.get("display_name", ""),
                    "real_name": member.get("real_name", ""),
                    "username": member.get("name", ""),
                    "email": profile.get("email", ""),
                    "is_bot": member.get("is_bot", False),
                    "deleted": member.get("deleted", False),
                }
            )
        cursor = data.get("response_metadata", {}).get("next_cursor", "")
        if not cursor:
            break
    return users


def list_open_issues(issue_repo: str, token: str) -> list[dict[str, Any]]:
    owner, repo = issue_repo.split("/", 1)
    page = 1
    issues: list[dict[str, Any]] = []
    while True:
        data = api_get(
            "https://api.github.com/repos/"
            f"{owner}/{repo}/issues?state=open&labels=CI%20auto%20triage&per_page=100&page={page}",
            token,
        )
        batch = [item for item in data if "pull_request" not in item]
        if not batch:
            break
        issues.extend(
            {
                "number": item.get("number", ""),
                "title": item.get("title", ""),
                "body": item.get("body", ""),
                "url": item.get("html_url", ""),
                "labels": item.get("labels", []),
            }
            for item in batch
        )
        if len(data) < 100:
            break
        page += 1
    return issues


def update_issue(issue_repo: str, issue_number: str | int, body: str, token: str, add_labels: list[str]) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(body)
        body_path = handle.name
    try:
        args = [
            "issue",
            "edit",
            str(issue_number),
            f"--repo={issue_repo}",
            f"--body-file={body_path}",
        ]
        for label in add_labels:
            args.append(f"--add-label={label}")
        gh(*args, token=token)
    finally:
        try:
            os.unlink(body_path)
        except FileNotFoundError:
            pass
