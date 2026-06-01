import unittest

from tools.ci.create_issues.__main__ import (
    _agent_excerpt_matches_signature,
    _regression_boundary_rejection_reason,
)


class CreateIssuesMainFiltersTests(unittest.TestCase):
    def test_boundary_allows_missing_last_passing(self) -> None:
        job = {"first_failing_sha": "abc123"}
        self.assertIsNone(_regression_boundary_rejection_reason(job))

    def test_boundary_allows_none_last_passing(self) -> None:
        job = {"last_passing_sha": None, "first_failing_sha": "abc123"}
        self.assertIsNone(_regression_boundary_rejection_reason(job))

    def test_boundary_rejects_when_first_failing_missing(self) -> None:
        job = {"last_passing_sha": "abc123"}
        self.assertEqual(_regression_boundary_rejection_reason(job), "missing first failing boundary")

    def test_boundary_rejects_when_same_sha(self) -> None:
        job = {"last_passing_sha": "abc123", "first_failing_sha": "abc123"}
        self.assertEqual(
            _regression_boundary_rejection_reason(job),
            "invalid regression boundary: last passing SHA equals first failing SHA",
        )

    def test_boundary_accepts_when_shas_differ(self) -> None:
        job = {"last_passing_sha": "abc123", "first_failing_sha": "def456"}
        self.assertIsNone(_regression_boundary_rejection_reason(job))

    def test_excerpt_match_accepts_substring(self) -> None:
        sig = "TT_FATAL: kernel missing fp32_dest_acc_en=true"
        excerpt = "Run 1 terminal error: TT_FATAL: kernel missing fp32_dest_acc_en=true in ComputeConfig."
        self.assertTrue(_agent_excerpt_matches_signature(sig, excerpt))

    def test_excerpt_match_rejects_unrelated_error(self) -> None:
        sig = "TT_FATAL: kernel missing fp32_dest_acc_en=true"
        excerpt = "AssertionError: expected 1 == 2"
        self.assertFalse(_agent_excerpt_matches_signature(sig, excerpt))


if __name__ == "__main__":
    unittest.main()
