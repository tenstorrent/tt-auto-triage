"""Shared GitHub REST API helpers."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_DEFAULT_UA = "tt-metal-ci-triage"


def github_api_get(
    token: str, endpoint: str, *, user_agent: str = _DEFAULT_UA,
) -> dict[str, Any]:
    """GET a GitHub REST API endpoint.  *endpoint* should start with ``/``."""
    req = urllib.request.Request(
        f"https://api.github.com{endpoint}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": user_agent,
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def github_user_info(
    token: str, username: str, *, user_agent: str = _DEFAULT_UA,
) -> dict[str, Any]:
    """Return the public profile for *username*, or ``{}`` on any error."""
    try:
        return github_api_get(
            token, f"/users/{urllib.parse.quote(username)}", user_agent=user_agent,
        )
    except Exception:
        return {}
