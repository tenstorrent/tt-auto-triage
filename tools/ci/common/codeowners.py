"""CODEOWNERS parsing helpers."""

from __future__ import annotations

import fnmatch
from pathlib import Path


def parse_codeowners(
    path: Path, *, keep_teams: bool = True,
) -> list[tuple[str, list[str]]]:
    """Parse a CODEOWNERS file into ``(pattern, owners)`` tuples.

    When *keep_teams* is ``False``, ``@org/team`` handles are excluded (only
    individual ``@user`` handles are kept).
    """
    if not path.exists():
        return []
    rules: list[tuple[str, list[str]]] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        pattern = parts[0].lstrip("/")
        owners: list[str] = []
        for tok in parts[1:]:
            if not tok.startswith("@"):
                continue
            handle = tok[1:]
            if not keep_teams and "/" in handle:
                continue
            if handle:
                owners.append(handle)
        if owners:
            rules.append((pattern, owners))
    return rules


def codeowners_match(path: str, pattern: str) -> bool:
    """Return whether *path* matches a CODEOWNERS *pattern*."""
    p = path.lstrip("/")
    pat = pattern.lstrip("/")
    if fnmatch.fnmatch(p, pat):
        return True
    if pat.endswith("/") and p.startswith(pat):
        return True
    if "/" not in pat and fnmatch.fnmatch(Path(p).name, pat):
        return True
    return False


def parse_codeowners_logins(path: Path) -> list[str]:
    """Return a flat, deduplicated list of individual logins (no teams)."""
    out: list[str] = []
    seen: set[str] = set()
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines() if path.exists() else []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        for tok in line.split()[1:]:
            if not tok.startswith("@"):
                continue
            login = tok[1:].strip()
            if not login or "/" in login:
                continue
            low = login.lower()
            if low in seen:
                continue
            seen.add(low)
            out.append(login)
    return out
