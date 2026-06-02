import unittest

from tools.ci.create_issues.detect_failures import _parse_job_url


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
