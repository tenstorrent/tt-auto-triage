"""Tests for the `SKIP_ALREADY_ASSIGNED` workflow feature.

Pins the contracts:

1. When SKIP_ALREADY_ASSIGNED=true, an issue that already has a named-owner
   recommendation comment is skipped BEFORE any expensive work: no
   pipeline_reorg load, no Slack download, no identity-index build, and
   critically no `resolve_owner` / agent call.
2. A recommendation comment whose body is the "unresolved" placeholder does
   NOT count as already-assigned — we retry those because they represent a
   prior failed resolution.
3. Scoped runs honor the skip too: passing a specific issue number for an
   already-assigned issue still short-circuits and emits no new comment.
4. With SKIP_ALREADY_ASSIGNED unset/false, behavior is identical to the
   legacy path — the prior `unchanged` bucket still handles idempotency but
   resolve_owner is called on every issue.
5. When every issue is skipped, the run short-circuits entirely and never
   touches pipeline / Slack / identity APIs.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tools.ci.assign_owners import __main__ as mod


def _issue(num: int) -> dict:
    return {
        "number": num,
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


def _named_owner_comment(name: str = "Alice A.") -> dict:
    return {"id": 1, "body": f"{mod.COMMENT_MARKER}\n**Recommended owner:** {name}\n"}


def _unresolved_comment() -> dict:
    return {"id": 1, "body": f"{mod.COMMENT_MARKER}\n**Recommended owner:** {mod._UNRESOLVED_OWNER_BODY}\n"}


class HasNamedOwnerCommentTests(unittest.TestCase):
    def test_empty_comment_list(self) -> None:
        self.assertFalse(mod._has_named_owner_comment([]))

    def test_non_marker_comment_does_not_count(self) -> None:
        self.assertFalse(mod._has_named_owner_comment([{"body": "just a human comment"}]))

    def test_named_owner_counts(self) -> None:
        self.assertTrue(mod._has_named_owner_comment([_named_owner_comment()]))

    def test_unresolved_placeholder_does_not_count(self) -> None:
        """Prior runs that failed to resolve should be retried, not treated as done."""
        self.assertFalse(mod._has_named_owner_comment([_unresolved_comment()]))

    def test_marker_with_malformed_body_does_not_count(self) -> None:
        # Marker present, body is only the placeholder text somewhere: still skip
        self.assertFalse(mod._has_named_owner_comment([{
            "body": f"{mod.COMMENT_MARKER}\n**Recommended owner:** {mod._UNRESOLVED_OWNER_BODY}\n"
        }]))


class SkipAlreadyAssignedIntegrationTests(unittest.TestCase):
    """main() end-to-end with SKIP_ALREADY_ASSIGNED toggled on/off."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.summary = Path(self._tmp.name) / "summary.md"

    def _base_env(self, **overrides: str):
        env = {
            "ISSUE_WRITE_TOKEN": "tok",
            "CURSOR_API_KEY": "k",
            "ISSUE_NUMBERS": "",
            "SUMMARY_OUTPUT": str(self.summary),
            "SKIP_ALREADY_ASSIGNED": "",
            **overrides,
        }
        return patch.dict("os.environ", env, clear=False)

    def _reload_module_constants(self):
        """main() reads SKIP_ALREADY_ASSIGNED at import time. Tests need to
        patch the already-computed module attribute, not os.environ."""
        return patch.object(mod, "SKIP_ALREADY_ASSIGNED", True)

    def _patch_summary(self):
        """Same story for SUMMARY_OUTPUT — snapshot at import time."""
        return patch.object(mod, "SUMMARY_OUTPUT", str(self.summary))

    def test_skip_on_prevents_any_expensive_setup_when_all_issues_assigned(self) -> None:
        """If every issue is already assigned, main() must not call pipeline
        load, Slack download, identity index, or resolve_owner. The whole point
        of the feature is 'super fast / do nothing'."""
        with self._base_env(), \
             self._reload_module_constants(), \
             self._patch_summary(), \
             patch.object(mod, "list_open_issues", return_value=[_issue(888), _issue(885)]), \
             patch.object(mod, "list_issue_comments", return_value=[_named_owner_comment()]), \
             patch.object(mod, "load_pipeline_reorg_owners") as fake_load, \
             patch.object(mod, "download_slack_directory") as fake_slack, \
             patch.object(mod, "build_identity_index") as fake_ident, \
             patch.object(mod, "resolve_owner") as fake_resolve, \
             patch.object(mod, "create_issue_comment") as fake_create, \
             patch.object(mod, "update_issue_comment") as fake_update, \
             patch.object(mod, "ISSUE_REPO", "ebanerjeeTT/issue_dump"), \
             patch.object(mod, "ISSUE_WRITE_TOKEN", "tok"):
            rc = mod.main()

        self.assertEqual(rc, 0)
        fake_load.assert_not_called()
        fake_slack.assert_not_called()
        fake_ident.assert_not_called()
        fake_resolve.assert_not_called()
        fake_create.assert_not_called()
        fake_update.assert_not_called()

        text = self.summary.read_text()
        self.assertIn("Skipped (already assigned):** 2", text)
        self.assertIn("#888", text)
        self.assertIn("#885", text)

    def test_skip_on_still_processes_issues_without_owner_comments(self) -> None:
        """Mixed case: one issue already assigned, one brand new. Only the
        new one should reach resolve_owner."""
        def comments_for(repo: str, num: int, _tok: str) -> list[dict]:
            return [_named_owner_comment()] if num == 888 else []

        with self._base_env(), \
             self._reload_module_constants(), \
             self._patch_summary(), \
             patch.object(mod, "list_open_issues", return_value=[_issue(888), _issue(885)]), \
             patch.object(mod, "list_issue_comments", side_effect=comments_for), \
             patch.object(mod, "load_pipeline_reorg_owners", return_value={}), \
             patch.object(mod, "build_identity_index", return_value={}), \
             patch.object(mod, "has_assignee_markers", return_value=False), \
             patch.object(mod, "add_issue_labels"), \
             patch.object(mod, "resolve_owner", return_value={
                 "source": "agent", "github_assignees": [], "github_names": [],
                 "slack_assignees": [], "slack_names": ["Bob B."],
             }) as fake_resolve, \
             patch.object(mod, "create_issue_comment", return_value={}) as fake_create, \
             patch.object(mod, "ISSUE_REPO", "ebanerjeeTT/issue_dump"), \
             patch.object(mod, "ISSUE_WRITE_TOKEN", "tok"):
            rc = mod.main()

        self.assertEqual(rc, 0)
        fake_resolve.assert_called_once()
        fake_create.assert_called_once()
        self.assertEqual(fake_create.call_args.args[1], 885)

        text = self.summary.read_text()
        self.assertIn("Skipped (already assigned):** 1", text)
        self.assertIn("#885", text)  # newly assigned

    def test_skip_on_unresolved_placeholder_is_retried(self) -> None:
        """Prior 'unresolved' placeholders are not real owners — retry them."""
        with self._base_env(), \
             self._reload_module_constants(), \
             self._patch_summary(), \
             patch.object(mod, "list_open_issues", return_value=[_issue(888)]), \
             patch.object(mod, "list_issue_comments", return_value=[_unresolved_comment()]), \
             patch.object(mod, "load_pipeline_reorg_owners", return_value={}), \
             patch.object(mod, "build_identity_index", return_value={}), \
             patch.object(mod, "has_assignee_markers", return_value=False), \
             patch.object(mod, "add_issue_labels"), \
             patch.object(mod, "resolve_owner", return_value={
                 "source": "agent", "github_assignees": ["x"], "github_names": ["X."],
                 "slack_assignees": [], "slack_names": [],
             }) as fake_resolve, \
             patch.object(mod, "update_issue_comment", return_value={}) as fake_update, \
             patch.object(mod, "ISSUE_REPO", "ebanerjeeTT/issue_dump"), \
             patch.object(mod, "ISSUE_WRITE_TOKEN", "tok"):
            rc = mod.main()

        self.assertEqual(rc, 0)
        fake_resolve.assert_called_once()
        fake_update.assert_called_once()  # existing placeholder comment gets updated

    def test_skip_on_scoped_run_with_assigned_issue_does_nothing(self) -> None:
        """User input: 'assign #888'. #888 already has an owner. Must no-op
        in seconds — no pipeline, no resolve_owner, no Slack, no agent."""
        with self._base_env(ISSUE_NUMBERS="888"), \
             self._reload_module_constants(), \
             self._patch_summary(), \
             patch.object(mod, "get_issue", return_value=_issue(888)), \
             patch.object(mod, "list_open_issues") as fake_list, \
             patch.object(mod, "list_issue_comments", return_value=[_named_owner_comment()]), \
             patch.object(mod, "load_pipeline_reorg_owners") as fake_load, \
             patch.object(mod, "build_identity_index") as fake_ident, \
             patch.object(mod, "resolve_owner") as fake_resolve, \
             patch.object(mod, "ISSUE_REPO", "ebanerjeeTT/issue_dump"), \
             patch.object(mod, "ISSUE_WRITE_TOKEN", "tok"):
            rc = mod.main()

        self.assertEqual(rc, 0)
        fake_list.assert_not_called()
        fake_load.assert_not_called()
        fake_ident.assert_not_called()
        fake_resolve.assert_not_called()

        text = self.summary.read_text()
        self.assertIn("#888", text)
        self.assertIn("already assigned", text)

    def test_skip_off_preserves_legacy_behavior(self) -> None:
        """With the flag off, every issue goes through resolve_owner even if a
        comment already exists (the pre-feature 'unchanged' path still catches
        idempotency, but the resolver is invoked)."""
        with self._base_env(), \
             patch.object(mod, "SKIP_ALREADY_ASSIGNED", False), \
             self._patch_summary(), \
             patch.object(mod, "list_open_issues", return_value=[_issue(888)]), \
             patch.object(mod, "list_issue_comments", return_value=[_named_owner_comment()]), \
             patch.object(mod, "load_pipeline_reorg_owners", return_value={}), \
             patch.object(mod, "build_identity_index", return_value={}), \
             patch.object(mod, "has_assignee_markers", return_value=False), \
             patch.object(mod, "add_issue_labels"), \
             patch.object(mod, "resolve_owner", return_value={
                 "source": "agent", "github_assignees": [], "github_names": [],
                 "slack_assignees": [], "slack_names": ["Alice A."],
             }) as fake_resolve, \
             patch.object(mod, "update_issue_comment", return_value={}), \
             patch.object(mod, "ISSUE_REPO", "ebanerjeeTT/issue_dump"), \
             patch.object(mod, "ISSUE_WRITE_TOKEN", "tok"):
            rc = mod.main()

        self.assertEqual(rc, 0)
        fake_resolve.assert_called_once()

    def test_skip_on_failure_to_read_comments_records_failure_and_continues(self) -> None:
        """If the comment-read fails during the skip pre-pass, the issue is
        recorded as failed (not silently downgraded to 'unassigned'). Other
        issues still run."""
        def comments_for(repo: str, num: int, _tok: str) -> list[dict]:
            if num == 888:
                raise RuntimeError("403 permission denied")
            return []

        with self._base_env(), \
             self._reload_module_constants(), \
             self._patch_summary(), \
             patch.object(mod, "list_open_issues", return_value=[_issue(888), _issue(885)]), \
             patch.object(mod, "list_issue_comments", side_effect=comments_for), \
             patch.object(mod, "load_pipeline_reorg_owners", return_value={}), \
             patch.object(mod, "build_identity_index", return_value={}), \
             patch.object(mod, "has_assignee_markers", return_value=False), \
             patch.object(mod, "add_issue_labels"), \
             patch.object(mod, "resolve_owner", return_value={
                 "source": "agent", "github_assignees": [], "github_names": [],
                 "slack_assignees": [], "slack_names": ["Dev"],
             }), \
             patch.object(mod, "create_issue_comment", return_value={}), \
             patch.object(mod, "ISSUE_REPO", "ebanerjeeTT/issue_dump"), \
             patch.object(mod, "ISSUE_WRITE_TOKEN", "tok"):
            rc = mod.main()

        self.assertEqual(rc, 1)  # we saw a failure, exit non-zero
        text = self.summary.read_text()
        self.assertIn("#888", text)
        self.assertIn("comment read failed", text)
        self.assertIn("#885", text)  # survived


if __name__ == "__main__":
    unittest.main()
