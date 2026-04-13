import time
import unittest

from tools.ci.slack_notify.issue_state import (
    parse_assignee_markers,
    should_notify_issue,
    upsert_slack_markers,
)


class SlackNotifyIssueStateTests(unittest.TestCase):
    def test_should_notify_issue_requires_assignee_markers(self) -> None:
        issue = {
            "body": "plain body",
            "labels": [{"name": "CI auto triage"}],
        }

        self.assertFalse(should_notify_issue(issue))

    def test_should_notify_issue_skips_previously_sent_issue(self) -> None:
        issue = {
            "body": "\n".join(
                [
                    "<!-- AUTO-TRIAGE-METADATA-START -->",
                    "`Auto-triage-assignees-gh: [\"alice\"]`",
                    "`Auto-triage-assignees-slack: [\"UALICE\"]`",
                    "`Auto-triage-assignee-source: CODEOWNERS`",
                    "`Auto-triage-slack-status: sent`",
                    "<!-- AUTO-TRIAGE-METADATA-END -->",
                ]
            ),
            "labels": [{"name": "CI auto triage"}, {"name": "auto-triage:owners-ready"}],
        }

        self.assertFalse(should_notify_issue(issue))

    def test_should_notify_issue_skips_issue_marked_sending(self) -> None:
        issue = {
            "body": "\n".join(
                [
                    "<!-- AUTO-TRIAGE-METADATA-START -->",
                    "`Auto-triage-assignees-gh: [\"alice\"]`",
                    "`Auto-triage-assignees-slack: [\"UALICE\"]`",
                    "`Auto-triage-assignee-source: CODEOWNERS`",
                    "`Auto-triage-slack-status: sending`",
                    f"`Auto-triage-slack-ts: {time.time()}`",
                    "<!-- AUTO-TRIAGE-METADATA-END -->",
                ]
            ),
            "labels": [{"name": "CI auto triage"}, {"name": "auto-triage:owners-ready"}],
        }

        self.assertFalse(should_notify_issue(issue))

    def test_should_notify_issue_retries_stale_sending_state(self) -> None:
        issue = {
            "body": "\n".join(
                [
                    "<!-- AUTO-TRIAGE-METADATA-START -->",
                    "`Auto-triage-assignees-gh: [\"alice\"]`",
                    "`Auto-triage-assignees-slack: [\"UALICE\"]`",
                    "`Auto-triage-assignee-source: CODEOWNERS`",
                    "`Auto-triage-slack-status: sending`",
                    "`Auto-triage-slack-ts: 1`",
                    "<!-- AUTO-TRIAGE-METADATA-END -->",
                ]
            ),
            "labels": [{"name": "CI auto triage"}, {"name": "auto-triage:owners-ready"}],
        }

        self.assertTrue(should_notify_issue(issue))

    def test_upsert_slack_markers_records_send_state(self) -> None:
        body = "\n".join(
            [
                "<!-- AUTO-TRIAGE-METADATA-START -->",
                "`Auto-triage-assignees-gh: [\"alice\"]`",
                "`Auto-triage-assignees-slack: [\"UALICE\"]`",
                "`Auto-triage-assignee-source: CODEOWNERS`",
                "<!-- AUTO-TRIAGE-METADATA-END -->",
            ]
        )

        updated = upsert_slack_markers(
            body,
            channel="C0APK6215B5",
            status="sent",
            ts="1740000000.000100",
        )

        assignees = parse_assignee_markers(updated)
        self.assertEqual(assignees["slack_assignees"], ["UALICE"])
        self.assertIn("`Auto-triage-slack-channel: C0APK6215B5`", updated)
        self.assertIn("`Auto-triage-slack-status: sent`", updated)
        self.assertIn("`Auto-triage-slack-ts: 1740000000.000100`", updated)


if __name__ == "__main__":
    unittest.main()
