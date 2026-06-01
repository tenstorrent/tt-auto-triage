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

    def test_ignores_generic_terminal_exit_code_when_specific_error_exists(self) -> None:
        log = "\n".join(
            [
                "FAILED UnitTest.RealFailure",
                "some cleanup output",
                "Error: Process completed with exit code 1.",
            ]
        )
        sig = _regex_extract_error(log)
        self.assertIn("RealFailure", sig)
        self.assertNotIn("exit code 1", sig.lower())

    def test_falls_back_to_generic_exit_code_when_no_specific_error(self) -> None:
        log = "\n".join(
            [
                "step output line",
                "Error: Process completed with exit code 1.",
            ]
        )
        sig = _regex_extract_error(log)
        self.assertIn("exit code 1", sig.lower())

    def test_specific_error_wins_even_when_generic_appears_earlier(self) -> None:
        log = "\n".join(
            [
                "Error: Process completed with exit code 1.",
                "later output",
                "AssertionError: tensor mismatch",
            ]
        )
        sig = _regex_extract_error(log)
        self.assertIn("AssertionError", sig)

    def test_returns_latest_generic_when_only_generic_signatures_exist(self) -> None:
        log = "\n".join(
            [
                "Error: Process completed with exit code 1.",
                "more output",
                "Error: Process completed with exit code 137.",
            ]
        )
        sig = _regex_extract_error(log)
        self.assertIn("exit code 137", sig.lower())

    def test_treats_plain_error_exit_code_as_generic(self) -> None:
        log = "\n".join(
            [
                "setup output",
                "Error: exit code 1",
            ]
        )
        sig = _regex_extract_error(log)
        self.assertEqual(sig.lower(), "error: exit code 1")

    def test_does_not_treat_specific_error_with_exit_code_text_as_generic(self) -> None:
        log = "\n".join(
            [
                "FAILED UnitTest.TerminalCase",
                "AssertionError: expected 1 == 2 (then process completed with exit code 1)",
            ]
        )
        sig = _regex_extract_error(log)
        self.assertIn("AssertionError", sig)


if __name__ == "__main__":
    unittest.main()
