"""Tests for turning a Slack export into error records.

The record produced here is the contract every later stage depends on, in
particular field 7, the raw Slack timestamp. These fixtures mirror the shape
get_and_analyze_slack.py writes.
"""

import json
import os
from datetime import datetime

import pytest

import extract_errors

NOW = 1_767_000_000.0
RUNS = "https://github.com/tenstorrent/tt-metal/actions/runs"

MESSAGE = 0
URL = 1
DISPLAY_TS = 2
JOB_NAME = 3
WORKFLOW = 4
IS_ND = 5
REPORT_LINK = 6
UNIX_TS = 7


def reply(*, unix=NOW, url=f"{RUNS}/2001/job/800001", failure_message="boom",
          workflow="All post-commit tests", job="build-artifact / device-tests",
          scenario="", cancelled=False, full_text_extra=None):
    full_text = ["Auto-triage cancelled:"] if cancelled else ["Auto-triage report:"]
    full_text += full_text_extra or []
    return {
        "date": datetime.fromtimestamp(unix).strftime("%B %d, %Y"),
        "timestamp": f"{unix:.6f}",
        "full_report_link": "https://github.com/tenstorrent/tt-auto-triage/actions/runs/7777",
        "failing_workflow": workflow,
        "failing_job": job,
        "failing_test": "",
        "failing_run": f"Run #1 ({url})" if url else "Run #1",
        "scenario": scenario,
        "failure_message": failure_message,
        "relevant_developers": "",
        "relevant_files": "",
        "notes": "",
        "full_text": full_text,
    }


@pytest.fixture
def run_extract(tmp_path, monkeypatch):
    """Run the extractor over an export and hand back the records."""

    def run(entries):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "build_slack_export_with_threads.json").write_text(
            json.dumps(entries), encoding="utf-8"
        )
        extract_errors.main()
        return json.loads((tmp_path / "all_errors.json").read_text(encoding="utf-8"))

    return run


class TestRecordShape:
    def test_record_has_eight_fields(self, run_extract):
        records = run_extract([reply()])

        assert len(records) == 1 and len(records[0]) == 8

    def test_raw_unix_timestamp_is_carried(self, run_extract):
        records = run_extract([reply(unix=NOW)])

        assert records[0][UNIX_TS] == pytest.approx(NOW)

    def test_display_and_raw_timestamps_agree(self, run_extract):
        records = run_extract([reply(unix=NOW)])
        local = datetime.fromtimestamp(records[0][UNIX_TS])

        assert records[0][DISPLAY_TS].startswith(local.strftime("%B ") + str(local.day))

    def test_missing_timestamp_yields_none_rather_than_a_guess(self, run_extract):
        entry = reply()
        entry["timestamp"] = ""

        records = run_extract([entry])

        assert records[0][UNIX_TS] is None
        assert records[0][DISPLAY_TS] is None

    def test_unparseable_timestamp_yields_none(self, run_extract):
        entry = reply()
        entry["timestamp"] = "not-a-timestamp"

        records = run_extract([entry])

        assert records[0][UNIX_TS] is None

    def test_report_link_is_carried(self, run_extract):
        records = run_extract([reply()])

        assert records[0][REPORT_LINK].endswith("/7777")


class TestSkipping:
    def test_entry_without_a_failure_message_is_skipped(self, run_extract):
        assert run_extract([reply(failure_message="")]) == []

    def test_placeholder_failure_message_is_skipped(self, run_extract):
        assert run_extract([reply(failure_message="---")]) == []

    def test_whitespace_failure_message_is_skipped(self, run_extract):
        assert run_extract([reply(failure_message="   \n  ")]) == []


class TestFieldExtraction:
    def test_url_is_pulled_out_of_the_failing_run_text(self, run_extract):
        records = run_extract([reply(url=f"{RUNS}/2001/job/800001")])

        assert records[0][URL] == f"{RUNS}/2001/job/800001"

    def test_absent_url_is_none(self, run_extract):
        records = run_extract([reply(url=None)])

        assert records[0][URL] is None

    def test_direct_fields_are_preferred(self, run_extract):
        records = run_extract([reply(workflow="wf-direct", job="job-direct")])

        assert records[0][JOB_NAME] == "job-direct"
        assert records[0][WORKFLOW] == "wf-direct"

    def test_names_are_recovered_from_full_text_when_fields_are_empty(self, run_extract):
        """Cancelled triage messages carry the names in the body instead."""
        records = run_extract([
            reply(workflow="", job="", cancelled=True,
                  full_text_extra=["Workflow: blackhole-post-commit",
                                   "Job: blackhole-multi-card (P300) / CCL APC test"])
        ])

        assert records[0][WORKFLOW] == "blackhole-post-commit"
        assert records[0][JOB_NAME] == "blackhole-multi-card (P300) / CCL APC test"


class TestNonDeterministicFlag:
    def test_scenario_outside_tt_metal_marks_nd(self, run_extract):
        records = run_extract([reply(scenario="Failure likely outside tt-metal")])

        assert records[0][IS_ND] is True

    def test_cancelled_triage_marks_nd(self, run_extract):
        records = run_extract([reply(cancelled=True)])

        assert records[0][IS_ND] is True

    def test_ordinary_failure_is_not_nd(self, run_extract):
        records = run_extract([reply()])

        assert records[0][IS_ND] is False


class TestDateRange:
    def test_entries_before_the_start_are_dropped(self, run_extract, monkeypatch):
        old = NOW - 60 * 86400
        start = datetime.fromtimestamp(NOW - 30 * 86400).strftime("%B %d, %Y")
        monkeypatch.setattr(extract_errors, "DATE_RANGE_START", start)

        assert run_extract([reply(unix=old)]) == []

    def test_entries_after_the_end_are_dropped(self, run_extract, monkeypatch):
        end = datetime.fromtimestamp(NOW - 30 * 86400).strftime("%B %d, %Y")
        monkeypatch.setattr(extract_errors, "DATE_RANGE_END", end)

        assert run_extract([reply(unix=NOW)]) == []

    def test_entries_inside_the_range_are_kept(self, run_extract, monkeypatch):
        start = datetime.fromtimestamp(NOW - 30 * 86400).strftime("%B %d, %Y")
        monkeypatch.setattr(extract_errors, "DATE_RANGE_START", start)

        assert len(run_extract([reply(unix=NOW)])) == 1
