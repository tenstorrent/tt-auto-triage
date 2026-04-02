"""Shared timestamp / datetime utilities."""

from __future__ import annotations

import datetime as dt
from datetime import datetime, timezone


def now_utc() -> str:
    """ISO 8601 UTC timestamp with second precision (trailing ``Z``)."""
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def now_utc_dt() -> dt.datetime:
    """Current UTC datetime (microsecond precision)."""
    return dt.datetime.now(dt.UTC)


now_iso = now_utc


def parse_iso_utc(text: str | None) -> dt.datetime | None:
    """Parse an ISO 8601 timestamp, returning ``None`` on failure."""
    if not text:
        return None
    value = text.strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def iso_utc(ts: float) -> str:
    """Convert a POSIX timestamp to an ISO 8601 UTC string."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
