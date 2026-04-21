"""Idempotency: running main() twice against the same issue body + labels writes once."""
import os
import unittest
from unittest.mock import patch

from tools.ci.assign_owners import __main__ as mod
from tools.ci.assign_owners.issue_state import upsert_assignee_markers

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

    def test_second_run_with_same_markers_and_label_skips_update(self) -> None:
        slack_dir = [{"id": "U1", "deleted": False, "real_name": "Alice", "display_name": "alice"}]
        pipeline = [{"name": "Job Alpha", "id": "U1", "owner_name": "Alice"}]

        # First run: issue has no assignee markers yet -> should write.
        issue_cold = {"number": 1, "body": _BASE_BODY, "labels": []}

        # Second run: body already has the expected markers and label -> should skip.
        body_warm = upsert_assignee_markers(
            _BASE_BODY, github_assignees=[], slack_assignees=["U1"], source="pipeline_reorg",
        )
        issue_warm = {"number": 1, "body": body_warm, "labels": [{"name": "auto-triage:owners-ready"}]}

        env = {
            "ISSUE_WRITE_TOKEN": "tok",
            "CURSOR_API_KEY": "ck",
            "SLACK_BOT_TOKEN": "",
            "SUMMARY_OUTPUT": "",
        }
        calls: list = []

        def fake_update(*args, **kwargs) -> None:
            calls.append((args, kwargs))

        with patch.dict(os.environ, env, clear=False), \
             patch.object(mod, "ISSUE_WRITE_TOKEN", "tok"), \
             patch.object(mod, "list_open_issues", side_effect=[[issue_cold], [issue_warm]]), \
             patch.object(mod, "load_pipeline_reorg_owners", return_value=pipeline), \
             patch.object(mod, "download_slack_directory", return_value=slack_dir), \
             patch.object(mod, "update_issue", side_effect=fake_update), \
             patch("urllib.request.urlopen") as fake_url:
            fake_url.return_value.__enter__.return_value.status = 204
            # Bypass Slack download (no SLACK_BOT_TOKEN in env).
            rc1 = mod.main()
            rc2 = mod.main()

        self.assertEqual(rc1, 0)
        self.assertEqual(rc2, 0)
        self.assertEqual(len(calls), 1, f"expected single update write, saw {len(calls)}: {calls}")


if __name__ == "__main__":
    unittest.main()
