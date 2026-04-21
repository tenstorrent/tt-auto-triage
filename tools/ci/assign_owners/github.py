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


_TRANSIENT = ("502", "503", "504", "bad gateway", "service unavailable", "timeout", "i/o timeout")


def _gh(*args: str, token: str | None = None, retries: int = 3) -> str:
    env = {**os.environ, **({"GITHUB_TOKEN": token} if token else {})}
    last = ""
    for attempt in range(retries):
        p = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=30, env=env)
        if p.returncode == 0:
            return p.stdout
        last = p.stderr.strip()
        if attempt < retries - 1 and any(m in last.lower() for m in _TRANSIENT):
            time.sleep(2 ** (attempt + 1)); continue
        break
    raise RuntimeError(f"gh {' '.join(args[:4])}... failed: {last}")


def _api_get(url: str, token: str | None = None, retries: int = 3) -> Any:
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            if attempt < retries - 1:
                time.sleep(2 ** (attempt + 1)); continue
            raise RuntimeError(f"API request failed: {url}: {exc}") from exc
    raise RuntimeError(f"Exhausted retries for {url}")


def download_slack_directory(token: str) -> list[dict[str, Any]]:
    users: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params = {"limit": "200", **({"cursor": cursor} if cursor else {})}
        req = urllib.request.Request(f"https://slack.com/api/users.list?{urllib.parse.urlencode(params)}",
                                     headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        if not data.get("ok"):
            raise RuntimeError(f"Slack users.list failed: {data.get('error', 'unknown')}")
        for m in data.get("members", []):
            p = m.get("profile", {})
            users.append({"id": m.get("id", ""), "display_name": p.get("display_name", ""),
                          "real_name": m.get("real_name", ""), "email": p.get("email", ""),
                          "is_bot": m.get("is_bot", False), "deleted": m.get("deleted", False)})
        cursor = data.get("response_metadata", {}).get("next_cursor", "")
        if not cursor:
            return users


def list_open_issues(issue_repo: str, token: str) -> list[dict[str, Any]]:
    owner, repo = issue_repo.split("/", 1)
    page, out = 1, []
    while True:
        data = _api_get(f"https://api.github.com/repos/{owner}/{repo}/issues?state=open&labels=CI%20auto%20triage&per_page=100&page={page}", token)
        batch = [i for i in data if "pull_request" not in i]
        if not batch:
            return out
        out += [{"number": i.get("number", ""), "title": i.get("title", ""), "body": i.get("body", ""),
                 "url": i.get("html_url", ""), "labels": i.get("labels", [])} for i in batch]
        if len(data) < 100:
            return out
        page += 1


def update_issue(issue_repo: str, issue_number: str | int, body: str, token: str, add_labels: list[str]) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as h:
        h.write(body); body_path = h.name
    try:
        args = ["issue", "edit", str(issue_number), f"--repo={issue_repo}", f"--body-file={body_path}", *(f"--add-label={lb}" for lb in add_labels)]
        _gh(*args, token=token)
    finally:
        try: os.unlink(body_path)
        except FileNotFoundError: pass
