"""Tests for the Unix-first timestamp helpers."""

from datetime import datetime, timezone

import pytest

import timestamps

# 2026-01-09T00:59:26.500Z
SAMPLE_UNIX = 1_767_920_366.5


class TestUnixConversion:
    def test_iso_output_is_utc_with_milliseconds(self):
        assert timestamps.unix_to_iso_utc(SAMPLE_UNIX) == "2026-01-09T00:59:26.500Z"

    def test_accepts_a_numeric_string(self):
        assert timestamps.unix_to_iso_utc(str(SAMPLE_UNIX)) == "2026-01-09T00:59:26.500Z"

    def test_datetime_is_timezone_aware(self):
        assert timestamps.unix_to_datetime_utc(SAMPLE_UNIX).tzinfo == timezone.utc

    @pytest.mark.parametrize("value", [None, "", "not-a-number", []])
    def test_unusable_values_return_none(self, value):
        assert timestamps.unix_to_iso_utc(value) is None

    def test_year_is_taken_from_the_timestamp_not_the_clock(self):
        """The old string round-trip guessed the year from datetime.now()."""
        old = timestamps.unix_to_datetime_utc(1_609_459_200)  # 2021-01-01

        assert old.year == 2021


class TestFormatting:
    def test_renders_the_display_form(self):
        """The display form is local time, unlike everything stored or emitted."""
        local = datetime.fromtimestamp(SAMPLE_UNIX)

        formatted = timestamps.format_unix(SAMPLE_UNIX)

        assert formatted.startswith(local.strftime("%B ") + str(local.day))
        assert formatted.endswith(" seconds")

    @pytest.mark.parametrize(
        "day_unix,expected",
        [
            (datetime(2026, 1, 1, 12).timestamp(), "January 1st"),
            (datetime(2026, 1, 2, 12).timestamp(), "January 2nd"),
            (datetime(2026, 1, 3, 12).timestamp(), "January 3rd"),
            (datetime(2026, 1, 4, 12).timestamp(), "January 4th"),
            (datetime(2026, 1, 11, 12).timestamp(), "January 11th"),
            (datetime(2026, 1, 21, 12).timestamp(), "January 21st"),
        ],
    )
    def test_ordinal_suffixes(self, day_unix, expected):
        assert timestamps.format_unix(day_unix).startswith(expected)

    def test_unusable_input_returns_none(self):
        assert timestamps.format_unix("nonsense") is None


class TestResolveUnix:
    def test_prefers_the_raw_timestamp(self):
        resolved = timestamps.resolve_unix(SAMPLE_UNIX, "January 1st, 1:00am, 0.00 seconds")

        assert resolved == SAMPLE_UNIX

    def test_falls_back_to_the_display_string(self):
        formatted = timestamps.format_unix(SAMPLE_UNIX)

        resolved = timestamps.resolve_unix(None, formatted)

        assert resolved == pytest.approx(SAMPLE_UNIX, abs=1.0)

    def test_returns_none_when_nothing_is_usable(self):
        assert timestamps.resolve_unix(None, "") is None

    def test_a_string_timestamp_is_still_a_timestamp(self):
        assert timestamps.resolve_unix("1767920366.5", "") == SAMPLE_UNIX
