import unittest
from contextlib import ExitStack
from unittest.mock import patch

from tools.ci.create_issues.detect_failures import (
    MAX_CONSECUTIVE_MISSING,
    MAX_RUNS_SCANNED,
    MAX_SEARCH_SECONDS,
    _clear_run_jobs_cache,
    _parse_job_url,
    iter_failing_jobs,
    resolve_last_passing_boundary,
    streak_still_failing,
)


def _run(run_id, ts, conclusion, sha, **extra):
    return {
        "id": run_id,
        "created_at": ts,
        "conclusion": conclusion,
        "head_sha": sha,
        "html_url": f"https://github.com/o/r/actions/runs/{run_id}",
        "workflow_id": 555,
        **extra,
    }


def _job(name, conclusion, run_id):
    return {
        "name": name,
        "conclusion": conclusion,
        "html_url": f"https://github.com/o/r/actions/runs/{run_id}/job/{run_id}9",
    }


def _jobs_fake(jobs_by_run, counter=None):
    """Build a paginate_api fake serving per-run job lists.

    ``jobs_by_run`` maps run_id -> list of (job_name, conclusion). Runs absent
    from the mapping return no jobs at all.
    """

    def fake(url, key, token=None):
        run_id = int(url.split("/runs/")[1].split("/")[0])
        if counter is not None:
            counter.append(run_id)
        return [_job(name, concl, run_id) for name, concl in jobs_by_run.get(run_id, [])]

    return fake


class BoundaryLinkTests(unittest.TestCase):
    """First/last failing boundaries must reference the oldest/newest analyzed
    failing JOBs, not runs or an inferred streak start."""

    def setUp(self) -> None:
        _clear_run_jobs_cache()

    def test_first_and_last_failing_link_to_jobs(self) -> None:
        # Newest -> oldest. Two consecutive failures trigger the low-volume
        # threshold; an older failure (different error) and a passing run
        # precede them in history.
        runs = [
            _run(300, "2026-06-15T03:00:00Z", "failure", "sha_new"),
            _run(200, "2026-06-15T02:00:00Z", "failure", "sha_old"),
            _run(100, "2026-06-15T01:00:00Z", "failure", "sha_older"),
            _run(50, "2026-06-15T00:00:00Z", "success", "sha_pass"),
        ]

        with patch(
            "tools.ci.create_issues.detect_failures.paginate_api",
            side_effect=_jobs_fake({rid: [("build", "failure")] for rid in (300, 200, 100)}),
        ), patch("tools.ci.create_issues.detect_failures.time.sleep"):
            jobs = list(
                iter_failing_jobs(
                    [["My Workflow", runs]],
                    "o/r",
                    consecutive_low_volume=2,
                    high_volume_runs_per_day=100,
                )
            )

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        # Oldest analyzed run is run 200 (not the older run 100 with a
        # different error). First failing links to that run's JOB.
        self.assertEqual(job["first_failing_sha"], "sha_old")
        self.assertEqual(
            job["first_failing_url"],
            "https://github.com/o/r/actions/runs/200/job/2009",
        )
        # Most-recent failing links to the newest run's JOB.
        self.assertEqual(job["last_failing_sha"], "sha_new")
        self.assertEqual(
            job["last_failing_url"],
            "https://github.com/o/r/actions/runs/300/job/3009",
        )
        # Neither failing boundary points at a bare run URL.
        self.assertIn("/job/", job["first_failing_url"])
        self.assertIn("/job/", job["last_failing_url"])
        # Last passing is resolved separately by the caller, so the generator
        # must not spend API calls guessing at it.
        self.assertNotIn("last_passing_sha", job)
        self.assertNotIn("last_passing_url", job)


class LastPassingBoundaryTests(unittest.TestCase):
    """Last passing must be the most recent run where THIS job succeeded —
    workflow-level success is a different (and usually wrong) question."""

    def setUp(self) -> None:
        _clear_run_jobs_cache()

    def _resolve(self, runs, jobs_by_run, counter=None, api_get=None):
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "tools.ci.create_issues.detect_failures.paginate_api",
                    side_effect=_jobs_fake(jobs_by_run, counter),
                )
            )
            stack.enter_context(patch("tools.ci.create_issues.detect_failures.time.sleep"))
            if api_get is not None:
                stack.enter_context(
                    patch("tools.ci.create_issues.detect_failures.api_get", side_effect=api_get)
                )
            return resolve_last_passing_boundary("build", runs, "o/r", token="t")

    def test_uses_job_success_when_workflow_run_failed(self) -> None:
        # The #52401 case: run 100 failed overall because a sibling job failed,
        # but the job we care about passed in it.
        runs = [
            _run(300, "2026-06-15T03:00:00Z", "failure", "sha_new"),
            _run(200, "2026-06-15T02:00:00Z", "failure", "sha_old"),
            _run(100, "2026-06-15T01:00:00Z", "failure", "sha_pass"),
        ]
        jobs_by_run = {
            300: [("build", "failure")],
            200: [("build", "failure")],
            100: [("build", "success"), ("sibling", "failure")],
        }

        found = self._resolve(runs, jobs_by_run)

        self.assertIsNotNone(found)
        self.assertEqual(found["head_sha"], "sha_pass")
        self.assertEqual(found["created_at"], "2026-06-15T01:00:00Z")
        self.assertEqual(found["job_url"], "https://github.com/o/r/actions/runs/100/job/1009")

    def test_returns_none_when_job_never_passed(self) -> None:
        runs = [
            _run(300, "2026-06-15T03:00:00Z", "failure", "sha_new"),
            _run(200, "2026-06-15T02:00:00Z", "failure", "sha_old"),
        ]
        jobs_by_run = {300: [("build", "failure")], 200: [("build", "failure")]}

        found = self._resolve(
            runs, jobs_by_run, api_get=lambda url, token=None: {"workflow_runs": []}
        )

        self.assertIsNone(found)

    def test_skips_runs_where_job_absent(self) -> None:
        # A run that never scheduled this job must not end the search.
        runs = [
            _run(300, "2026-06-15T03:00:00Z", "failure", "sha_new"),
            _run(200, "2026-06-15T02:00:00Z", "failure", "sha_skip"),
            _run(100, "2026-06-15T01:00:00Z", "failure", "sha_pass"),
        ]
        jobs_by_run = {
            300: [("build", "failure")],
            200: [("other", "failure")],
            100: [("build", "success")],
        }

        found = self._resolve(runs, jobs_by_run)

        self.assertIsNotNone(found)
        self.assertEqual(found["head_sha"], "sha_pass")

    def test_ignores_manual_runs(self) -> None:
        runs = [
            _run(300, "2026-06-15T03:00:00Z", "failure", "sha_new"),
            _run(250, "2026-06-15T02:30:00Z", "success", "sha_manual", event="workflow_dispatch"),
            _run(100, "2026-06-15T01:00:00Z", "failure", "sha_pass"),
        ]
        jobs_by_run = {
            300: [("build", "failure")],
            250: [("build", "success")],
            100: [("build", "success")],
        }

        found = self._resolve(runs, jobs_by_run)

        self.assertEqual(found["head_sha"], "sha_pass")

    def test_dedupes_repeated_run_attempts(self) -> None:
        # The snapshot stores one entry per attempt; the same run must only be
        # fetched (and charged against the budget) once.
        runs = [
            _run(300, "2026-06-15T03:00:00Z", "failure", "sha_new"),
            _run(300, "2026-06-15T03:00:00Z", "failure", "sha_new"),
            _run(100, "2026-06-15T01:00:00Z", "failure", "sha_pass"),
        ]
        jobs_by_run = {300: [("build", "failure")], 100: [("build", "success")]}
        fetched: list[int] = []

        found = self._resolve(runs, jobs_by_run, counter=fetched)

        self.assertEqual(found["head_sha"], "sha_pass")
        self.assertEqual(fetched, [300, 100])

    def test_respects_missing_job_budget(self) -> None:
        # A renamed job would otherwise drag the walk across all of history.
        total = MAX_CONSECUTIVE_MISSING + 10
        runs = [
            _run(1000 + i, f"2026-06-15T00:{i:02d}:00Z", "failure", f"sha{i}")
            for i in range(total, 0, -1)
        ]
        jobs_by_run = {run["id"]: [("renamed", "failure")] for run in runs}
        fetched: list[int] = []

        found = self._resolve(runs, jobs_by_run, counter=fetched)

        self.assertIsNone(found)
        self.assertEqual(len(fetched), MAX_CONSECUTIVE_MISSING)

    def test_respects_run_scan_budget(self) -> None:
        total = MAX_RUNS_SCANNED + 15
        runs = [
            _run(1000 + i, f"2026-06-15T00:{i:02d}:00Z", "failure", f"sha{i}")
            for i in range(total, 0, -1)
        ]
        jobs_by_run = {run["id"]: [("build", "failure")] for run in runs}
        fetched: list[int] = []

        found = self._resolve(runs, jobs_by_run, counter=fetched)

        self.assertIsNone(found)
        self.assertEqual(len(fetched), MAX_RUNS_SCANNED)

    def test_falls_back_to_older_history(self) -> None:
        # The job last passed before the snapshot's rolling window.
        runs = [
            _run(300, "2026-06-15T03:00:00Z", "failure", "sha_new"),
            _run(200, "2026-06-15T02:00:00Z", "failure", "sha_old"),
        ]
        older = _run(50, "2026-05-01T00:00:00Z", "failure", "sha_ancient")
        jobs_by_run = {
            300: [("build", "failure")],
            200: [("build", "failure")],
            50: [("build", "success")],
        }

        def fake_api_get(url, token=None):
            self.assertIn("/actions/workflows/555/runs", url)
            return {"workflow_runs": [older]}

        found = self._resolve(runs, jobs_by_run, api_get=fake_api_get)

        self.assertIsNotNone(found)
        self.assertEqual(found["head_sha"], "sha_ancient")
        self.assertEqual(found["job_url"], "https://github.com/o/r/actions/runs/50/job/509")

    def test_respects_time_budget(self) -> None:
        # Big pipelines make each run's job list slow to fetch, so the run
        # count alone does not bound the search; the clock has to.
        runs = [
            _run(1000 + i, f"2026-06-15T00:{i:02d}:00Z", "failure", f"sha{i}")
            for i in range(MAX_RUNS_SCANNED, 0, -1)
        ]
        jobs_by_run = {run["id"]: [("build", "failure")] for run in runs}
        fetched: list[int] = []

        # Time stands still long enough to seed the deadline and scan a couple
        # of runs, then jumps past it.
        ticks = {"n": 0}

        def fake_clock():
            ticks["n"] += 1
            return 0.0 if ticks["n"] <= 3 else MAX_SEARCH_SECONDS + 1.0

        with patch("tools.ci.create_issues.detect_failures.time.time", fake_clock):
            found = self._resolve(runs, jobs_by_run, counter=fetched)

        self.assertIsNone(found)
        # Stopped on the clock, well before the run-count cap.
        self.assertLess(len(fetched), MAX_RUNS_SCANNED)

    def test_returns_none_for_empty_runs(self) -> None:
        self.assertIsNone(resolve_last_passing_boundary("build", [], "o/r", token="t"))


class StreakStillFailingTests(unittest.TestCase):
    """The live re-check guards against stale snapshots: a newer passing run
    means the failure streak has already recovered and we must not file."""

    JOB = {
        "job_name": "ttsim-tests / sdpa group",
        "job_urls": [
            "https://github.com/o/r/actions/runs/100/job/1009",  # newest analyzed
            "https://github.com/o/r/actions/runs/90/job/909",
        ],
    }

    def _api_get(self, newest_live_run, run_jobs):
        """Build an api_get fake driven by the URL being requested."""

        def fake(url, token=None):
            if "/actions/runs/100" in url and "/jobs" not in url:
                return {"workflow_id": 555}
            if "/workflows/555/runs" in url:
                return {"workflow_runs": [newest_live_run]}
            raise AssertionError(f"unexpected api_get url: {url}")

        return fake

    def _paginate(self, run_jobs):
        def fake(url, key, token=None):
            return run_jobs
        return fake

    def test_proceeds_when_no_newer_run(self) -> None:
        # Newest live run is the same one we analyzed → streak intact.
        live = {"id": 100, "event": "push", "html_url": "u"}
        with patch("tools.ci.create_issues.detect_failures.api_get", side_effect=self._api_get(live, [])):
            still, reason = streak_still_failing(self.JOB, "o/r", token="t")
        self.assertTrue(still)
        self.assertEqual(reason, "")

    def test_proceeds_when_job_still_fails_in_newer_run(self) -> None:
        live = {"id": 200, "event": "push", "html_url": "https://github.com/o/r/actions/runs/200"}
        jobs = [{"name": "ttsim-tests / sdpa group", "conclusion": "failure"}]
        with patch("tools.ci.create_issues.detect_failures.api_get", side_effect=self._api_get(live, jobs)), \
             patch("tools.ci.create_issues.detect_failures.paginate_api", side_effect=self._paginate(jobs)):
            still, reason = streak_still_failing(self.JOB, "o/r", token="t")
        self.assertTrue(still)
        self.assertEqual(reason, "")

    def test_skips_when_job_passed_in_newer_run(self) -> None:
        live = {"id": 200, "event": "push", "html_url": "https://github.com/o/r/actions/runs/200"}
        jobs = [{"name": "ttsim-tests / sdpa group", "conclusion": "success"}]
        with patch("tools.ci.create_issues.detect_failures.api_get", side_effect=self._api_get(live, jobs)), \
             patch("tools.ci.create_issues.detect_failures.paginate_api", side_effect=self._paginate(jobs)):
            still, reason = streak_still_failing(self.JOB, "o/r", token="t")
        self.assertFalse(still)
        self.assertIn("streak broken", reason)
        self.assertIn("passed", reason)

    def test_skips_when_job_absent_from_newer_run(self) -> None:
        live = {"id": 200, "event": "push", "html_url": "https://github.com/o/r/actions/runs/200"}
        jobs = [{"name": "some other job", "conclusion": "success"}]
        with patch("tools.ci.create_issues.detect_failures.api_get", side_effect=self._api_get(live, jobs)), \
             patch("tools.ci.create_issues.detect_failures.paginate_api", side_effect=self._paginate(jobs)):
            still, reason = streak_still_failing(self.JOB, "o/r", token="t")
        self.assertFalse(still)
        self.assertIn("absent", reason)

    def test_fails_open_on_api_error(self) -> None:
        def boom(url, token=None):
            raise RuntimeError("network down")

        with patch("tools.ci.create_issues.detect_failures.api_get", side_effect=boom):
            still, reason = streak_still_failing(self.JOB, "o/r", token="t")
        self.assertTrue(still)
        self.assertEqual(reason, "")


class ParseJobUrlTests(unittest.TestCase):
    def test_parses_run_and_job_ids(self) -> None:
        run_id, job_id = _parse_job_url(
            "https://github.com/tenstorrent/tt-metal/actions/runs/26806354901/job/79026942195"
        )
        self.assertEqual(run_id, 26806354901)
        self.assertEqual(job_id, 79026942195)

    def test_raises_for_invalid_url(self) -> None:
        with self.assertRaises(ValueError):
            _parse_job_url("https://github.com/tenstorrent/tt-metal/actions/runs/26806354901")


if __name__ == "__main__":
    unittest.main()
