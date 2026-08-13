#!/usr/bin/env python3
"""Timestamp helpers shared across the grouping pipeline.

Slack gives us Unix timestamps. Those are carried through the pipeline as-is and
converted to UTC only at the point of output. The human-readable form is kept
alongside for logs and issue-free reporting, but is never parsed back into a
date: doing so loses the timezone and the year, which is why the formatted
string used to be re-derived incorrectly around year boundaries.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
import re


def format_unix(unix_timestamp: float) -> Optional[str]:
    """Render a Unix timestamp as e.g. 'January 3rd, 5:32pm, 26.43 seconds'."""
    try:
        ts_float = float(unix_timestamp)
    except (TypeError, ValueError):
        return None

    try:
        dt = datetime.fromtimestamp(ts_float)
    except (OSError, OverflowError, ValueError):
        return None

    day = dt.day
    if 10 <= day % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")

    hour_12 = dt.strftime("%I").lstrip("0") or "12"
    time_part = f"{hour_12}:{dt.strftime('%M')}{dt.strftime('%p').lower()}"
    seconds_part = f"{ts_float % 60:.2f} seconds"

    return f"{dt.strftime('%B')} {day}{suffix}, {time_part}, {seconds_part}"


def unix_to_datetime_utc(unix_timestamp) -> Optional[datetime]:
    """Convert a Unix timestamp to a timezone-aware UTC datetime."""
    if unix_timestamp is None or unix_timestamp == "":
        return None
    try:
        return datetime.fromtimestamp(float(unix_timestamp), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def unix_to_iso_utc(unix_timestamp) -> Optional[str]:
    """Convert a Unix timestamp to ISO 8601 UTC with milliseconds."""
    dt = unix_to_datetime_utc(unix_timestamp)
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{int(dt.microsecond / 1000):03d}Z"


def parse_formatted_to_datetime(timestamp_str: str) -> Optional[datetime]:
    """Parse the legacy display format back into a UTC datetime.

    Only used as a fallback for state written before Unix timestamps were
    carried through. The format omits the year and the timezone, so the result
    is a best guess.
    """
    if not timestamp_str:
        return None

    parts = timestamp_str.split(", ")
    if len(parts) < 2:
        return None

    now = datetime.now()
    date_part = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", parts[0])
    try:
        dt_local = datetime.strptime(f"{date_part} {now.year}, {parts[1]}", "%B %d %Y, %I:%M%p")
    except ValueError:
        return None

    if len(parts) >= 3:
        seconds_match = re.search(r"(\d+\.?\d*)", parts[2])
        if seconds_match:
            seconds_value = float(seconds_match.group(1))
            dt_local = dt_local.replace(
                second=int(seconds_value) % 60,
                microsecond=int((seconds_value % 1) * 1_000_000),
            )

    if dt_local > now + timedelta(days=180):
        dt_local = dt_local.replace(year=now.year - 1)
    elif dt_local < now - timedelta(days=180) and now.month == 1 and dt_local.month == 12:
        dt_local = dt_local.replace(year=now.year - 1)

    # astimezone() on a naive datetime uses the local offset in effect on that
    # date, so a January timestamp read during daylight saving is not shifted.
    return dt_local.astimezone(timezone.utc)


def resolve_unix(unix_timestamp=None, formatted: str = "") -> Optional[float]:
    """Prefer a real Unix timestamp, falling back to parsing the display form."""
    if unix_timestamp is not None and unix_timestamp != "":
        try:
            return float(unix_timestamp)
        except (TypeError, ValueError):
            pass

    dt = parse_formatted_to_datetime(formatted)
    return dt.timestamp() if dt else None
