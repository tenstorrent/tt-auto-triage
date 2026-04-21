"""Tests for the scoped-run feature: `ISSUE_NUMBERS` env / `issue-numbers`
workflow input.

Pins two contracts:

1. `parse_issue_numbers` tolerates the shapes a human would naturally paste
   into a workflow_dispatch form: bare numbers, `#123`, full GitHub issue
   URLs, comma- and/or whitespace-separated, with duplicates.
2. When `ISSUE_NUMBERS` is set, the driver hits `get_issue` per number and
   never calls `list_open_issues`. This is the whole point — it's supposed
   to short-circuit the full-repo scan.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tools.ci.assign_owners import __main__ as mod


class ParseIssueNumbersTests(unittest.TestCase):
    def test_empty_input_yields_empty_list(self) -> None:
        self.assertEqual(mod.parse_issue_numbers(""), [])
        self.assertEqual(mod.parse_issue_numbers("   "), [])

    def test_bare_number(self) -> None:
        self.assertEqual(mod.parse_issue_numbers("888"), [888])

    def test_hash_prefix(self) -> None:
        self.assertEqual(mod.parse_issue_numbers("#888"), [888])

    def test_full_github_url(self) -> None:
        self.assertEqual(
            mod.parse_issue_numbers("https://github.com/ebanerjeeTT/issue_dump/issues/888"),
            [888],
        )

    def test_mixed_shapes_and_separators(self) -> None:
        raw = "888, #885\nhttps://github.com/ebanerjeeTT/issue_dump/issues/887  886"
        self.assertEqual(mod.parse_issue_numbers(raw), [888, 885, 887, 886])

    def test_deduplicates_preserving_order(self) -> None:
        raw = "888 #888 https://github.com/ebanerjeeTT/issue_dump/issues/888 885"
        self.assertEqual(mod.parse_issue_numbers(raw), [888, 885])

    def test_non_numeric_tokens_are_skipped(self) -> None:
        self.assertEqual(mod.parse_issue_numbers("not-a-number, 123, also-bad"), [123])


class ScopedRunIntegrationTests(unittest.TestCase):
    """When ISSUE_NUMBERS is set, main() must use get_issue and skip list_open_issues."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.summary = Path(self._tmp.name) / "summary.md"
        # Minimal issue body that parse_base_markers accepts.
        self.issue = {
            "number": 888,
            "title": "t",
            "body": (
                "<!-- AUTO-TRIAGE-METADATA-START -->\n"
                "Auto-triage-workflow: WF\n"
                "Auto-triage-job-name: JOB\n"
                "<!-- AUTO-TRIAGE-METADATA-END -->\n"
            ),
            "url": "",
            "labels": [{"name": mod.OWNERS_READY_LABEL}],
            "state": "open",
        }

    def _patch_env(self, **overrides: str):
        env = {
            "ISSUE_WRITE_TOKEN": "tok",
            "CURSOR_API_KEY": "k",
            "ISSUE_NUMBERS": "",
            "SUMMARY_OUTPUT": str(self.summary),
            **overrides,
        }
        return patch.dict("os.environ", env, clear=False)

    def test_scoped_run_uses_get_issue_and_skips_list(self) -> None:
        with self._patch_env(ISSUE_NUMBERS="#888, https://github.com/x/y/issues/885"), \
             patch.object(mod, "get_issue") as fake_get, \
             patch.object(mod, "list_open_issues") as fake_list, \
             patch.object(mod, "load_pipeline_reorg_owners", return_value=[]), \
             patch.object(mod, "build_identity_index", return_value={}), \
             patch.object(mod, "resolve_owner", return_value={
                 "source": "agent", "github_assignees": [], "github_names": [],
                 "slack_assignees": [], "slack_names": ["Dev"],
             }), \
             patch.object(mod, "has_assignee_markers", return_value=False), \
             patch.object(mod, "add_issue_labels"), \
             patch.object(mod, "list_issue_comments", return_value=[]), \
             patch.object(mod, "create_issue_comment", return_value={}), \
             patch.object(mod, "ISSUE_REPO", "ebanerjeeTT/issue_dump"), \
             patch.object(mod, "ISSUE_WRITE_TOKEN", "tok"):
            fake_get.side_effect = [{**self.issue, "number": 888}, {**self.issue, "number": 885}]
            rc = mod.main()

        self.assertEqual(rc, 0)
        fake_list.assert_not_called()
        self.assertEqual(fake_get.call_count, 2)
        called_numbers = [c.args[1] for c in fake_get.call_args_list]
        self.assertEqual(called_numbers, [888, 885])

    def test_unscoped_run_uses_list_open_issues(self) -> None:
        with self._patch_env(ISSUE_NUMBERS=""), \
             patch.object(mod, "get_issue") as fake_get, \
             patch.object(mod, "list_open_issues", return_value=[]) as fake_list, \
             patch.object(mod, "load_pipeline_reorg_owners", return_value=[]), \
             patch.object(mod, "build_identity_index", return_value={}), \
             patch.object(mod, "ISSUE_REPO", "ebanerjeeTT/issue_dump"), \
             patch.object(mod, "ISSUE_WRITE_TOKEN", "tok"):
            rc = mod.main()

        self.assertEqual(rc, 0)
        fake_get.assert_not_called()
        fake_list.assert_called_once()

    def test_scoped_run_skips_issues_that_fail_to_fetch(self) -> None:
        with self._patch_env(ISSUE_NUMBERS="888 885"), \
             patch.object(mod, "get_issue") as fake_get, \
             patch.object(mod, "list_open_issues") as fake_list, \
             patch.object(mod, "load_pipeline_reorg_owners", return_value=[]), \
             patch.object(mod, "build_identity_index", return_value={}), \
             patch.object(mod, "resolve_owner", return_value={
                 "source": "agent", "github_assignees": [], "github_names": [],
                 "slack_assignees": [], "slack_names": ["Dev"],
             }), \
             patch.object(mod, "has_assignee_markers", return_value=False), \
             patch.object(mod, "add_issue_labels"), \
             patch.object(mod, "list_issue_comments", return_value=[]), \
             patch.object(mod, "create_issue_comment", return_value={}), \
             patch.object(mod, "ISSUE_REPO", "ebanerjeeTT/issue_dump"), \
             patch.object(mod, "ISSUE_WRITE_TOKEN", "tok"), \
             patch.object(mod, "SUMMARY_OUTPUT", str(self.summary)):
            fake_get.side_effect = [RuntimeError("404 not found"), {**self.issue, "number": 885}]
            rc = mod.main()

        self.assertEqual(rc, 0)
        fake_list.assert_not_called()
        self.assertEqual(fake_get.call_count, 2)
        # Missing issue must not abort the whole run; the surviving one is
        # in the summary exactly once.
        self.assertTrue(self.summary.exists())
        text = self.summary.read_text()
        self.assertIn("#885", text)
        self.assertNotIn("#888", text)


if __name__ == "__main__":
    unittest.main()
