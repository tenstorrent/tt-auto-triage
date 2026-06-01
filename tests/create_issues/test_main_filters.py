import unittest

from tools.ci.create_issues.__main__ import (
    _agent_excerpt_matches_signature,
    _has_clear_regression_boundary,
)


class CreateIssuesMainFiltersTests(unittest.TestCase):
    def test_boundary_is_unclear_when_missing_sha(self) -> None:
        job = {"first_failing_sha": "abc123"}
        self.assertFalse(_has_clear_regression_boundary(job))

    def test_boundary_is_unclear_when_sha_is_none(self) -> None:
        job = {"last_passing_sha": None, "first_failing_sha": "abc123"}
        self.assertFalse(_has_clear_regression_boundary(job))

    def test_boundary_is_unclear_when_same_sha(self) -> None:
        job = {"last_passing_sha": "abc123", "first_failing_sha": "abc123"}
        self.assertFalse(_has_clear_regression_boundary(job))

    def test_boundary_is_clear_when_shas_differ(self) -> None:
        job = {"last_passing_sha": "abc123", "first_failing_sha": "def456"}
        self.assertTrue(_has_clear_regression_boundary(job))

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
