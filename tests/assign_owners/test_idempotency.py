"""Idempotency: running main() twice posts exactly one recommendation comment."""
import os
import unittest
from unittest.mock import patch

from tools.ci.assign_owners import __main__ as mod

_BASE_BODY = "\n".join([
    "Body text",
    "<!-- AUTO-TRIAGE-METADATA-START -->",
    "`Auto-triage-workflow: Workflow A`",
    "`Auto-triage-job-name: Job Alpha`",
    "`Auto-triage-fingerprint: fp-1`",
    "<!-- AUTO-TRIAGE-METADATA-END -->",
])


class IdempotencyTests(unittest.TestCase):
    def setUp(self) -> None:
        mod._active_cache.clear()

    def _run_twice(self, issue_cold: dict, issue_warm: dict) -> dict[str, list]:
        slack_dir = [{"id": "U1", "deleted": False, "real_name": "Alice", "display_name": "alice"}]
        pipeline = {"Job Alpha": {"name": "Job Alpha", "id": "U1", "owner_name": "Alice"}}
        env = {"ISSUE_WRITE_TOKEN": "tok", "CURSOR_API_KEY": "ck", "SLACK_BOT_TOKEN": "", "SUMMARY_OUTPUT": ""}
        calls: dict[str, list] = {"create": [], "update_comment": [], "update_issue": [], "add_labels": []}
        # Seed the comment list with what a first-run create would have posted.
        posted: list[dict] = []

        def fake_create(repo: str, num, body: str, token: str) -> dict:
            comment = {"id": 101, "body": body}
            posted.append(comment)
            calls["create"].append(body)
            return comment

        def fake_update_comment(repo: str, cid, body: str, token: str) -> None:
            calls["update_comment"].append((cid, body))

        def fake_update_issue(*args, **kwargs) -> None:
            calls["update_issue"].append((args, kwargs))

        def fake_add_labels(*args, **kwargs) -> None:
            calls["add_labels"].append((args, kwargs))

        def fake_list_comments(repo: str, num, token: str) -> list:
            return list(posted)

        with patch.dict(os.environ, env, clear=False), \
             patch.object(mod, "ISSUE_WRITE_TOKEN", "tok"), \
             patch.object(mod, "SKIP_ALREADY_ASSIGNED", True), \
             patch.object(mod, "list_open_issues", side_effect=[[issue_cold], [issue_warm]]), \
             patch.object(mod, "load_pipeline_reorg_owners", return_value=pipeline), \
             patch.object(mod, "download_slack_directory", return_value=slack_dir), \
             patch.object(mod, "build_identity_index", return_value={}), \
             patch.object(mod, "update_issue", side_effect=fake_update_issue), \
             patch.object(mod, "add_issue_labels", side_effect=fake_add_labels), \
             patch.object(mod, "create_issue_comment", side_effect=fake_create), \
             patch.object(mod, "update_issue_comment", side_effect=fake_update_comment), \
             patch.object(mod, "list_issue_comments", side_effect=fake_list_comments), \
             patch("urllib.request.urlopen") as fake_url:
            fake_url.return_value.__enter__.return_value.status = 204
            rc1 = mod.main()
            rc2 = mod.main()
        self.assertEqual(rc1, 0)
        self.assertEqual(rc2, 0)
        return calls

    def test_second_run_skips_create_and_update(self) -> None:
        issue_cold = {"number": 1, "body": _BASE_BODY, "labels": [{"name": "auto-triage:owners-ready"}]}
        # Second run: same body, label still present. The seeded comment from run 1 is already posted.
        issue_warm = {"number": 1, "body": _BASE_BODY, "labels": [{"name": "auto-triage:owners-ready"}]}
        calls = self._run_twice(issue_cold, issue_warm)
        self.assertEqual(len(calls["create"]), 1, f"expected exactly one comment create, got {calls['create']}")
        self.assertEqual(calls["update_comment"], [], "no comment PATCH should fire on the idempotent second run")
        self.assertEqual(calls["update_issue"], [], "body has no stale markers -> no issue body edit")

    def test_growing_history_no_op_when_recommendation_is_unchanged(self) -> None:
        """A refresh that re-resolves to the SAME owner must NOT shuffle them
        from `Recommended` into `Previous owners` and back. That would create
        endless visual churn on every refresh. With Alice already recommended
        and the resolver picking Alice again, the comment body must match the
        existing one byte-for-byte and `update_issue_comment` must not fire."""
        slack_dir = [{"id": "U1", "deleted": False, "real_name": "Alice"}]
        pipeline = {"Job Alpha": {"name": "Job Alpha", "id": "U1", "owner_name": "Alice"}}
        env = {"ISSUE_WRITE_TOKEN": "tok", "CURSOR_API_KEY": "ck", "SLACK_BOT_TOKEN": "",
               "SUMMARY_OUTPUT": ""}
        existing = {
            "id": 99,
            "body": f"{mod.COMMENT_MARKER}\n**Recommended owner:** Alice\n",
        }

        update_calls: list = []

        def fake_update_comment(repo, cid, body, token) -> None:
            update_calls.append((cid, body))

        issue = {"number": 1, "body": _BASE_BODY, "labels": [{"name": "auto-triage:owners-ready"}]}
        with patch.dict(os.environ, env, clear=False), \
             patch.object(mod, "ISSUE_WRITE_TOKEN", "tok"), \
             patch.object(mod, "SKIP_ALREADY_ASSIGNED", False), \
             patch.object(mod, "REFRESH_OWNER_RECOMMENDATION", True), \
             patch.object(mod, "list_open_issues", return_value=[issue]), \
             patch.object(mod, "load_pipeline_reorg_owners", return_value=pipeline), \
             patch.object(mod, "download_slack_directory", return_value=slack_dir), \
             patch.object(mod, "build_identity_index", return_value={}), \
             patch.object(mod, "list_issue_comments", return_value=[existing]), \
             patch.object(mod, "create_issue_comment") as fake_create, \
             patch.object(mod, "update_issue_comment", side_effect=fake_update_comment), \
             patch.object(mod, "update_issue"), \
             patch.object(mod, "add_issue_labels"), \
             patch("urllib.request.urlopen") as fake_url:
            fake_url.return_value.__enter__.return_value.status = 204
            rc = mod.main()
        self.assertEqual(rc, 0)
        self.assertEqual(update_calls, [], "no PATCH should fire when refresh produces the same owner")
        fake_create.assert_not_called()

    def test_growing_history_moves_old_owner_into_previous_on_refresh(self) -> None:
        """A reassign that picks a DIFFERENT owner must move the old recommendation
        into `Previous owners` and put the new one as the recommendation."""
        slack_dir = [
            {"id": "U1", "deleted": False, "real_name": "Alice"},
            {"id": "U2", "deleted": False, "real_name": "Bob"},
        ]
        pipeline = {}
        env = {"ISSUE_WRITE_TOKEN": "tok", "CURSOR_API_KEY": "ck", "SLACK_BOT_TOKEN": "",
               "SUMMARY_OUTPUT": ""}
        existing = {
            "id": 99,
            "body": f"{mod.COMMENT_MARKER}\n**Recommended owner:** Alice\n",
        }
        captured: dict = {}

        def fake_update_comment(repo, cid, body, token) -> None:
            captured["body"] = body

        issue = {"number": 1, "body": _BASE_BODY, "labels": [{"name": "auto-triage:owners-ready"}]}
        with patch.dict(os.environ, env, clear=False), \
             patch.object(mod, "ISSUE_WRITE_TOKEN", "tok"), \
             patch.object(mod, "SKIP_ALREADY_ASSIGNED", False), \
             patch.object(mod, "REFRESH_OWNER_RECOMMENDATION", True), \
             patch.object(mod, "list_open_issues", return_value=[issue]), \
             patch.object(mod, "load_pipeline_reorg_owners", return_value=pipeline), \
             patch.object(mod, "download_slack_directory", return_value=slack_dir), \
             patch.object(mod, "build_identity_index", return_value={}), \
             patch.object(mod, "list_issue_comments", return_value=[existing]), \
             patch.object(mod, "resolve_owner", return_value={
                 "source": "agent", "github_assignees": [], "github_names": ["Bob"],
                 "slack_assignees": ["U2"], "slack_names": ["Bob"],
             }), \
             patch.object(mod, "update_issue_comment", side_effect=fake_update_comment), \
             patch.object(mod, "update_issue"), \
             patch.object(mod, "add_issue_labels"), \
             patch("urllib.request.urlopen") as fake_url:
            fake_url.return_value.__enter__.return_value.status = 204
            rc = mod.main()
        self.assertEqual(rc, 0)
        self.assertIn("**Previous owners:** Alice", captured["body"])
        self.assertIn("**Recommended owner:** Bob", captured["body"])

    def test_stale_assignee_markers_trigger_one_body_strip(self) -> None:
        stale = _BASE_BODY.replace(
            "`Auto-triage-fingerprint: fp-1`",
            "`Auto-triage-fingerprint: fp-1`\n`Auto-triage-assignees-gh: []`\n"
            "`Auto-triage-assignees-slack: [\"U1\"]`\n`Auto-triage-assignee-source: pipeline_reorg`",
        )
        issue_cold = {"number": 1, "body": stale, "labels": [{"name": "auto-triage:owners-ready"}]}
        # After run 1, stale markers are gone; run 2 operates on the cleaned body.
        cleaned = mod.strip_assignee_markers(stale)
        issue_warm = {"number": 1, "body": cleaned, "labels": [{"name": "auto-triage:owners-ready"}]}
        calls = self._run_twice(issue_cold, issue_warm)
        self.assertEqual(len(calls["update_issue"]), 1, "exactly one issue-body strip should fire across both runs")
        self.assertEqual(len(calls["create"]), 1)


if __name__ == "__main__":
    unittest.main()
