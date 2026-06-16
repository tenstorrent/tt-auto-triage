import unittest
from unittest.mock import patch

from tools.ci.create_issues.detect_failures import (
    _parse_job_url,
    iter_failing_jobs,
    streak_still_failing,
)


def _run(run_id, ts, conclusion, sha):
    return {
        "id": run_id,
        "created_at": ts,
        "conclusion": conclusion,
        "head_sha": sha,
        "html_url": f"https://github.com/o/r/actions/runs/{run_id}",
    }


class BoundaryLinkTests(unittest.TestCase):
    """First/last failing boundaries must reference the oldest/newest analyzed
    failing JOBs, not runs or an inferred streak start."""

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

        def fake_paginate(url, key, token):
            run_id = int(url.split("/runs/")[1].split("/")[0])
            return [
                {
                    "name": "build",
                    "conclusion": "failure",
                    "html_url": f"https://github.com/o/r/actions/runs/{run_id}/job/{run_id}9",
                }
            ]

        with patch(
            "tools.ci.create_issues.detect_failures.paginate_api",
            side_effect=fake_paginate,
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
        # Last passing remains a run URL.
        self.assertEqual(job["last_passing_url"], "https://github.com/o/r/actions/runs/50")
        # Neither failing boundary points at a bare run URL.
        self.assertIn("/job/", job["first_failing_url"])
        self.assertIn("/job/", job["last_failing_url"])


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
