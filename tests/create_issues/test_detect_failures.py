import unittest

from tools.ci.create_issues.detect_failures import _regex_extract_error


class RegexExtractErrorTests(unittest.TestCase):
    def test_uses_last_failed_line_not_first(self) -> None:
        log = "\n".join(
            [
                "running tests...",
                "FAILED UnitTest.FirstCase",
                "FAILED UnitTest.SecondCase",
                "FAILED UnitTest.TerminalCase",
                "exit code 1",
            ]
        )
        sig = _regex_extract_error(log)
        self.assertIn("TerminalCase", sig)
        self.assertNotIn("FirstCase", sig)

    def test_prefers_latest_match_across_patterns(self) -> None:
        log = "\n".join(
            [
                "TT_FATAL old failure in unrelated setup",
                "lots of output...",
                "AssertionError: expected 1 == 2",
            ]
        )
        sig = _regex_extract_error(log)
        self.assertIn("AssertionError", sig)
        self.assertNotIn("TT_FATAL", sig)

    def test_returns_empty_when_no_patterns_match(self) -> None:
        self.assertEqual(_regex_extract_error("all good\nexit code 0"), "")


if __name__ == "__main__":
    unittest.main()
