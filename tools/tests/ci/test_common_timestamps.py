"""Tests for tools.ci.common.timestamps."""

from __future__ import annotations

import datetime as dt
import re

from tools.ci.common.timestamps import iso_utc, now_iso, now_utc, now_utc_dt, parse_iso_utc

ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def test_now_utc_format():
    result = now_utc()
    assert ISO_Z_RE.match(result), f"unexpected format: {result}"


def test_now_utc_dt_returns_aware_datetime():
    result = now_utc_dt()
    assert isinstance(result, dt.datetime)
    assert result.tzinfo is not None


def test_now_iso_is_alias_for_now_utc():
    assert now_iso is now_utc


def test_parse_iso_utc_z_suffix():
    result = parse_iso_utc("2024-06-15T12:30:00Z")
    assert result is not None
    assert result.year == 2024
    assert result.month == 6
    assert result.hour == 12
    assert result.tzinfo is not None


def test_parse_iso_utc_offset():
    result = parse_iso_utc("2024-06-15T12:30:00+00:00")
    assert result is not None
    assert result.hour == 12


def test_parse_iso_utc_none_returns_none():
    assert parse_iso_utc(None) is None
    assert parse_iso_utc("") is None
    assert parse_iso_utc("   ") is None


def test_parse_iso_utc_invalid_returns_none():
    assert parse_iso_utc("not-a-date") is None


def test_iso_utc_epoch_zero():
    result = iso_utc(0.0)
    assert "1970-01-01" in result
