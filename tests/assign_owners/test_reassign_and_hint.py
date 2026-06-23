"""Tests for the `reassign-to-someone-else`, `refresh-owner-recommendation`,
and `extra-context-for-agent` workflow inputs.

Pinned contracts:

1. Validation: every illegal flag combination at the top of `main()` exits 1
   before any network call, so workflow_dispatch can't silently run the wrong
   thing.
2. Comment format round-trip: `_render_comment` and `_parse_recommendation_comment`
   handle empty bodies, legacy single-line bodies, and full growing-history
   bodies — including the unresolved placeholder.
3. Reassign mode: every prior recommendation (current + previous list) is
   reverse-looked-up into Slack IDs / GitHub logins and added to the per-issue
   `extra_ex` blacklist passed into `resolve_owner`.
4. Refresh mode: `extra_ex` stays empty; comment is still upserted in the
   growing-history format.
5. Extra-context mode: `resolve_owner` is called with `skip_fast_path=True`,
   so the deterministic `pipeline_reorg` path is never consulted; the prompt
   built by `_build_prompt` contains the raw dev hint between the EXTRA INPUT
   FROM DEVS banners.
6. Empty `extra_context`: the EXTRA INPUT FROM DEVS block (sentinels and
   contents) is stripped from the prompt entirely.
7. Reverse-lookup miss: a name that isn't in slack_dir or identity_index logs a
   warning and is omitted from `extra_ex` — the run still succeeds.
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


def _comment(body: str) -> dict:
    return {"id": 42, "body": body}


# -----------------------------------------------------------------------------
# Validation
# -----------------------------------------------------------------------------


class FlagValidationTests(unittest.TestCase):
    """Three illegal combinations must fail-fast with a clear message."""

    def _run_with(self, **flags: object) -> int:
        defaults = {
            "SKIP_ALREADY_ASSIGNED": True,
            "REASSIGN_TO_SOMEONE_ELSE": False,
            "REFRESH_OWNER_RECOMMENDATION": False,
            "EXTRA_CONTEXT_FOR_AGENT": "",
        }
        defaults.update(flags)
        with patch.dict("os.environ", {"ISSUE_WRITE_TOKEN": "t", "CURSOR_API_KEY": "k"}, clear=False), \
             patch.object(mod, "ISSUE_WRITE_TOKEN", "t"), \
             patch.multiple(mod, **defaults), \
             patch.object(mod, "list_open_issues", return_value=[]) as fake_list:
            rc = mod.main()
        # If validation fails, list_open_issues must NOT be called: validation
        # runs before any GH read so failed-fast actually means failed-fast.
        return rc, fake_list

    def test_skip_true_with_reassign_fails(self) -> None:
        rc, fake_list = self._run_with(SKIP_ALREADY_ASSIGNED=True, REASSIGN_TO_SOMEONE_ELSE=True)
        self.assertEqual(rc, 1)
        fake_list.assert_not_called()

    def test_skip_true_with_refresh_fails(self) -> None:
        rc, fake_list = self._run_with(SKIP_ALREADY_ASSIGNED=True, REFRESH_OWNER_RECOMMENDATION=True)
        self.assertEqual(rc, 1)
        fake_list.assert_not_called()

    def test_skip_true_with_extra_context_fails(self) -> None:
        rc, fake_list = self._run_with(SKIP_ALREADY_ASSIGNED=True, EXTRA_CONTEXT_FOR_AGENT="hint")
        self.assertEqual(rc, 1)
        fake_list.assert_not_called()

    def test_reassign_and_refresh_mutually_exclusive(self) -> None:
        rc, fake_list = self._run_with(
            SKIP_ALREADY_ASSIGNED=False,
            REASSIGN_TO_SOMEONE_ELSE=True, REFRESH_OWNER_RECOMMENDATION=True,
        )
        self.assertEqual(rc, 1)
        fake_list.assert_not_called()

    def test_skip_false_without_intent_fails(self) -> None:
        rc, fake_list = self._run_with(SKIP_ALREADY_ASSIGNED=False)
        self.assertEqual(rc, 1)
        fake_list.assert_not_called()

    def test_skip_true_alone_passes(self) -> None:
        rc, fake_list = self._run_with(SKIP_ALREADY_ASSIGNED=True)
        self.assertEqual(rc, 0)
        fake_list.assert_called_once()

    def test_skip_false_with_refresh_passes(self) -> None:
        rc, fake_list = self._run_with(SKIP_ALREADY_ASSIGNED=False, REFRESH_OWNER_RECOMMENDATION=True)
        self.assertEqual(rc, 0)
        fake_list.assert_called_once()

    def test_skip_false_with_extra_context_passes(self) -> None:
        rc, fake_list = self._run_with(SKIP_ALREADY_ASSIGNED=False, EXTRA_CONTEXT_FOR_AGENT="hint")
        self.assertEqual(rc, 0)
        fake_list.assert_called_once()


# -----------------------------------------------------------------------------
# Comment format
# -----------------------------------------------------------------------------


class CommentFormatTests(unittest.TestCase):
    def test_render_with_no_previous_omits_previous_line(self) -> None:
        out = mod._render_comment("Alice A.", [])
        self.assertIn("**Recommended owner:** Alice A.", out)
        self.assertNotIn("Previous owners:", out)

    def test_render_with_previous_emits_comma_separated_line(self) -> None:
        out = mod._render_comment("Sam Adesoye", ["Salar Hosseini", "Djordje Ivanovic"])
        self.assertIn("**Previous owners:** Salar Hosseini, Djordje Ivanovic", out)
        self.assertIn("**Recommended owner:** Sam Adesoye", out)
        # Recommended line must follow Previous, not the other way around — UI
        # order matters because reviewers scan top-to-bottom.
        self.assertLess(out.index("Previous owners:"), out.index("Recommended owner:"))

    def test_render_with_unresolved_placeholder_when_name_empty(self) -> None:
        out = mod._render_comment("", [])
        self.assertIn(mod._UNRESOLVED_OWNER_BODY, out)

    def test_parse_legacy_single_line_body(self) -> None:
        body = f"{mod.COMMENT_MARKER}\n**Recommended owner:** Alice A.\n"
        prev, current = mod._parse_recommendation_comment(body)
        self.assertEqual(prev, [])
        self.assertEqual(current, "Alice A.")

    def test_parse_growing_history_body(self) -> None:
        body = (
            f"{mod.COMMENT_MARKER}\n"
            "**Previous owners:** Salar Hosseini, Djordje Ivanovic\n"
            "**Recommended owner:** Sam Adesoye\n"
        )
        prev, current = mod._parse_recommendation_comment(body)
        self.assertEqual(prev, ["Salar Hosseini", "Djordje Ivanovic"])
        self.assertEqual(current, "Sam Adesoye")

    def test_parse_unresolved_placeholder_returns_empty_current(self) -> None:
        body = f"{mod.COMMENT_MARKER}\n**Recommended owner:** {mod._UNRESOLVED_OWNER_BODY}\n"
        prev, current = mod._parse_recommendation_comment(body)
        self.assertEqual(prev, [])
        self.assertEqual(current, "")

    def test_parse_empty_body(self) -> None:
        self.assertEqual(mod._parse_recommendation_comment(""), ([], ""))

    def test_round_trip_via_render_then_parse(self) -> None:
        body = mod._render_comment("Sam Adesoye", ["Salar Hosseini"])
        prev, current = mod._parse_recommendation_comment(body)
        self.assertEqual(prev, ["Salar Hosseini"])
        self.assertEqual(current, "Sam Adesoye")


# -----------------------------------------------------------------------------
# Reverse lookup
# -----------------------------------------------------------------------------


class IdentifiersForNameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.slack_dir = [
            {"id": "U1", "real_name": "Alice A.", "profile": {"display_name": "alice"}},
            {"id": "U2", "real_name": "Bob Builder", "profile": {"display_name": "bob"}},
        ]
        self.identity_index = {
            "U1": {"github_login": "alice-gh", "github_name": "Alice A."},
            "U2": {"github_login": "bob-gh", "github_name": "Bob Builder"},
        }

    def test_full_match_returns_both(self) -> None:
        sid, login = mod._identifiers_for_name("Alice A.", self.slack_dir, self.identity_index)
        self.assertEqual(sid, "U1")
        self.assertEqual(login, "alice-gh")

    def test_case_insensitive_match(self) -> None:
        sid, login = mod._identifiers_for_name("alice a.", self.slack_dir, self.identity_index)
        self.assertEqual(sid, "U1")
        self.assertEqual(login, "alice-gh")

    def test_miss_returns_blanks(self) -> None:
        sid, login = mod._identifiers_for_name("Nobody", self.slack_dir, self.identity_index)
        self.assertEqual((sid, login), ("", ""))

    def test_unique_name_assumption_first_hit_wins(self) -> None:
        slack_dir = [
            {"id": "Ufirst", "real_name": "Same Name"},
            {"id": "Usecond", "real_name": "Same Name"},
        ]
        sid, _ = mod._identifiers_for_name("Same Name", slack_dir, {})
        self.assertEqual(sid, "Ufirst")


# -----------------------------------------------------------------------------
# Prompt template injection
# -----------------------------------------------------------------------------


class PromptInjectionTests(unittest.TestCase):
    def test_extra_context_kept_when_present(self) -> None:
        prompt = mod._build_prompt(
            workflow_name="WF", job_name="JOB", ex_owner_note="",
            ex_employees_display="", extra_context="Take this to ttnncore.",
        )
        self.assertIn("EXTRA INPUT FROM DEVS", prompt)
        self.assertIn("Take this to ttnncore.", prompt)
        self.assertNotIn("EXTRA_INPUT_BLOCK_BEGIN", prompt)
        self.assertNotIn("EXTRA_INPUT_BLOCK_END", prompt)

    def test_extra_context_block_stripped_when_empty(self) -> None:
        prompt = mod._build_prompt(
            workflow_name="WF", job_name="JOB", ex_owner_note="",
            ex_employees_display="", extra_context="",
        )
        self.assertNotIn("EXTRA INPUT FROM DEVS", prompt)
        self.assertNotIn("EXTRA_INPUT_BLOCK_BEGIN", prompt)
        self.assertNotIn("EXTRA_INPUT_BLOCK_END", prompt)


# -----------------------------------------------------------------------------
# Main-loop integration: reassign / refresh / extra-context
# -----------------------------------------------------------------------------


class _IntegrationBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.summary = Path(self._tmp.name) / "summary.md"
        self.slack_dump = Path(self._tmp.name) / "slack_users.json"
        mod._active_cache.clear()

    def _env(self, **overrides: str):
        env = {
            "ISSUE_WRITE_TOKEN": "tok", "CURSOR_API_KEY": "k", "ISSUE_NUMBERS": "888",
            "SUMMARY_OUTPUT": str(self.summary), "SLACK_BOT_TOKEN": "",
        }
        env.update(overrides)
        return patch.dict("os.environ", env, clear=False)


class ReassignModeIntegrationTests(_IntegrationBase):
    def test_reassign_blacklists_current_and_previous_owners(self) -> None:
        # Existing comment has Salar in previous and Djordje as current. Reassign
        # should send BOTH names through reverse-lookup, building a 4-element
        # extra_ex set (slack_id + github_login for each).
        existing = (
            f"{mod.COMMENT_MARKER}\n"
            "**Previous owners:** Salar Hosseini\n"
            "**Recommended owner:** Djordje Ivanovic\n"
        )
        slack_dir = [
            {"id": "U1", "real_name": "Salar Hosseini"},
            {"id": "U2", "real_name": "Djordje Ivanovic"},
            {"id": "U3", "real_name": "Sam Adesoye"},
        ]
        identity = {
            "U1": {"github_login": "salar-gh", "github_name": "Salar Hosseini"},
            "U2": {"github_login": "djordje-gh", "github_name": "Djordje Ivanovic"},
            "U3": {"github_login": "sam-gh", "github_name": "Sam Adesoye"},
        }

        with self._env(SLACK_BOT_TOKEN="x"), \
             patch.object(mod, "SKIP_ALREADY_ASSIGNED", False), \
             patch.object(mod, "REASSIGN_TO_SOMEONE_ELSE", True), \
             patch.object(mod, "REFRESH_OWNER_RECOMMENDATION", False), \
             patch.object(mod, "EXTRA_CONTEXT_FOR_AGENT", ""), \
             patch.object(mod, "ISSUE_WRITE_TOKEN", "tok"), \
             patch.object(mod, "ISSUE_REPO", "ebanerjeeTT/issue_dump"), \
             patch.object(mod, "SUMMARY_OUTPUT", str(self.summary)), \
             patch.object(mod, "get_issue", return_value=_issue(888)), \
             patch.object(mod, "list_open_issues"), \
             patch.object(mod, "list_issue_comments", return_value=[_comment(existing)]), \
             patch.object(mod, "load_pipeline_reorg_owners", return_value={}), \
             patch.object(mod, "build_identity_index", return_value=identity), \
             patch.object(mod, "download_slack_directory", return_value=slack_dir), \
             patch.object(mod, "SLACK_DUMP_PATH", self.slack_dump), \
             patch.object(mod, "has_assignee_markers", return_value=False), \
             patch.object(mod, "add_issue_labels"), \
             patch.object(mod, "update_issue_comment", return_value={}) as fake_update, \
             patch.object(mod, "resolve_owner", return_value={
                 "source": "agent", "github_assignees": ["sam-gh"], "github_names": ["Sam Adesoye"],
                 "slack_assignees": ["U3"], "slack_names": ["Sam Adesoye"],
             }) as fake_resolve:
            rc = mod.main()

        self.assertEqual(rc, 0)
        fake_resolve.assert_called_once()
        kwargs = fake_resolve.call_args.kwargs
        self.assertIn("extra_ex", kwargs)
        # All four IDs (Salar's slack + login, Djordje's slack + login) must be
        # in the blacklist or we'll just re-pick the same person.
        self.assertEqual(kwargs["extra_ex"], frozenset({"U1", "salar-gh", "U2", "djordje-gh"}))
        self.assertEqual(kwargs["extra_context"], "")
        self.assertFalse(kwargs.get("skip_fast_path", False))

        # Comment was updated, growing-history format: Salar + Djordje go into
        # previous, new pick (Sam) becomes current.
        body_arg = fake_update.call_args.args[2]
        self.assertIn("**Previous owners:** Salar Hosseini, Djordje Ivanovic", body_arg)
        self.assertIn("**Recommended owner:** Sam Adesoye", body_arg)


class RefreshModeIntegrationTests(_IntegrationBase):
    def test_refresh_does_not_blacklist_anyone(self) -> None:
        existing = f"{mod.COMMENT_MARKER}\n**Recommended owner:** Djordje Ivanovic\n"
        slack_dir = [{"id": "U2", "real_name": "Djordje Ivanovic"}]
        identity = {"U2": {"github_login": "djordje-gh", "github_name": "Djordje Ivanovic"}}

        with self._env(SLACK_BOT_TOKEN="x"), \
             patch.object(mod, "SKIP_ALREADY_ASSIGNED", False), \
             patch.object(mod, "REASSIGN_TO_SOMEONE_ELSE", False), \
             patch.object(mod, "REFRESH_OWNER_RECOMMENDATION", True), \
             patch.object(mod, "EXTRA_CONTEXT_FOR_AGENT", ""), \
             patch.object(mod, "ISSUE_WRITE_TOKEN", "tok"), \
             patch.object(mod, "ISSUE_REPO", "ebanerjeeTT/issue_dump"), \
             patch.object(mod, "SUMMARY_OUTPUT", str(self.summary)), \
             patch.object(mod, "SLACK_DUMP_PATH", self.slack_dump), \
             patch.object(mod, "get_issue", return_value=_issue(888)), \
             patch.object(mod, "list_open_issues"), \
             patch.object(mod, "list_issue_comments", return_value=[_comment(existing)]), \
             patch.object(mod, "load_pipeline_reorg_owners", return_value={}), \
             patch.object(mod, "build_identity_index", return_value=identity), \
             patch.object(mod, "download_slack_directory", return_value=slack_dir), \
             patch.object(mod, "has_assignee_markers", return_value=False), \
             patch.object(mod, "add_issue_labels"), \
             patch.object(mod, "update_issue_comment", return_value={}), \
             patch.object(mod, "resolve_owner", return_value={
                 "source": "agent", "github_assignees": ["sam-gh"], "github_names": ["Sam Adesoye"],
                 "slack_assignees": ["U3"], "slack_names": ["Sam Adesoye"],
             }) as fake_resolve:
            rc = mod.main()

        self.assertEqual(rc, 0)
        fake_resolve.assert_called_once()
        kwargs = fake_resolve.call_args.kwargs
        self.assertEqual(kwargs.get("extra_ex", frozenset()), frozenset())
        self.assertFalse(kwargs.get("skip_fast_path", False))


class ExtraContextIntegrationTests(_IntegrationBase):
    def test_extra_context_forces_skip_fast_path_and_passes_through(self) -> None:
        with self._env(), \
             patch.object(mod, "SKIP_ALREADY_ASSIGNED", False), \
             patch.object(mod, "REASSIGN_TO_SOMEONE_ELSE", False), \
             patch.object(mod, "REFRESH_OWNER_RECOMMENDATION", False), \
             patch.object(mod, "EXTRA_CONTEXT_FOR_AGENT", "Pick someone from ttnncore."), \
             patch.object(mod, "ISSUE_WRITE_TOKEN", "tok"), \
             patch.object(mod, "ISSUE_REPO", "ebanerjeeTT/issue_dump"), \
             patch.object(mod, "SUMMARY_OUTPUT", str(self.summary)), \
             patch.object(mod, "SLACK_DUMP_PATH", self.slack_dump), \
             patch.object(mod, "get_issue", return_value=_issue(888)), \
             patch.object(mod, "list_open_issues"), \
             patch.object(mod, "list_issue_comments", return_value=[]), \
             patch.object(mod, "load_pipeline_reorg_owners", return_value={}), \
             patch.object(mod, "build_identity_index", return_value={}), \
             patch.object(mod, "has_assignee_markers", return_value=False), \
             patch.object(mod, "add_issue_labels"), \
             patch.object(mod, "create_issue_comment", return_value={}), \
             patch.object(mod, "resolve_owner", return_value={
                 "source": "agent", "github_assignees": ["x"], "github_names": ["X"],
                 "slack_assignees": ["U"], "slack_names": ["X"],
             }) as fake_resolve:
            rc = mod.main()

        self.assertEqual(rc, 0)
        kwargs = fake_resolve.call_args.kwargs
        self.assertTrue(kwargs.get("skip_fast_path"))
        self.assertEqual(kwargs.get("extra_context"), "Pick someone from ttnncore.")


class ReverseLookupMissTests(_IntegrationBase):
    def test_unidentifiable_previous_owner_does_not_abort_run(self) -> None:
        # Previous comment names a person we can't reverse-lookup. The blacklist
        # is empty for that name (warn-and-continue), but the run still finishes
        # and updates the comment.
        existing = f"{mod.COMMENT_MARKER}\n**Recommended owner:** Ghost Person\n"
        with self._env(), \
             patch.object(mod, "SKIP_ALREADY_ASSIGNED", False), \
             patch.object(mod, "REASSIGN_TO_SOMEONE_ELSE", True), \
             patch.object(mod, "REFRESH_OWNER_RECOMMENDATION", False), \
             patch.object(mod, "EXTRA_CONTEXT_FOR_AGENT", ""), \
             patch.object(mod, "ISSUE_WRITE_TOKEN", "tok"), \
             patch.object(mod, "ISSUE_REPO", "ebanerjeeTT/issue_dump"), \
             patch.object(mod, "SUMMARY_OUTPUT", str(self.summary)), \
             patch.object(mod, "SLACK_DUMP_PATH", self.slack_dump), \
             patch.object(mod, "get_issue", return_value=_issue(888)), \
             patch.object(mod, "list_open_issues"), \
             patch.object(mod, "list_issue_comments", return_value=[_comment(existing)]), \
             patch.object(mod, "load_pipeline_reorg_owners", return_value={}), \
             patch.object(mod, "build_identity_index", return_value={}), \
             patch.object(mod, "has_assignee_markers", return_value=False), \
             patch.object(mod, "add_issue_labels"), \
             patch.object(mod, "update_issue_comment", return_value={}), \
             patch.object(mod, "resolve_owner", return_value={
                 "source": "agent", "github_assignees": ["sam-gh"], "github_names": ["Sam Adesoye"],
                 "slack_assignees": ["U3"], "slack_names": ["Sam Adesoye"],
             }) as fake_resolve:
            rc = mod.main()

        self.assertEqual(rc, 0)
        kwargs = fake_resolve.call_args.kwargs
        # Ghost Person isn't in slack_dir or identity, so extra_ex stays empty.
        self.assertEqual(kwargs.get("extra_ex", frozenset()), frozenset())


if __name__ == "__main__":
    unittest.main()
