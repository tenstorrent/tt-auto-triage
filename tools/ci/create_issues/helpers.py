from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

_REDACTION_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "[REDACTED_SLACK_TOKEN]"),
    (
        re.compile(r"(?i)(authorization:\s*bearer\s+)[^\s`]+"),
        r"\1[REDACTED_TOKEN]",
    ),
    (
        re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s`]+"),
        r"\1[REDACTED_KEY]",
    ),
)


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


def sanitize_text(text: str) -> str:
    sanitized = text
    for pattern, replacement in _REDACTION_RULES:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


def fingerprint_for(workflow_name: str, job_name: str, signature: str) -> str:
    raw = f"{workflow_name}\n{job_name}\n{signature.strip().lower()}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]
