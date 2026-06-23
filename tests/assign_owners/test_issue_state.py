import unittest

from tools.ci.assign_owners.issue_state import (
    has_assignee_markers,
    parse_base_markers,
    strip_assignee_markers,
)


class AssignOwnersIssueStateTests(unittest.TestCase):
    def test_parse_base_markers_reads_pr1_contract(self) -> None:
        body = "\n".join(
            [
                "<!-- AUTO-TRIAGE-METADATA-START -->",
                "`Auto-triage-workflow: Workflow A`",
                "`Auto-triage-job-name: Job B`",
                "`Auto-triage-fingerprint: fp-123`",
                "<!-- AUTO-TRIAGE-METADATA-END -->",
            ]
        )

        self.assertEqual(
            parse_base_markers(body),
            {
                "workflow_name": "Workflow A",
                "job_name": "Job B",
                "fingerprint": "fp-123",
            },
        )

    def test_strip_assignee_markers_preserves_base_markers(self) -> None:
        original = "\n".join(
            [
                "body text",
                "",
                "<!-- AUTO-TRIAGE-METADATA-START -->",
                "`Auto-triage-workflow: Workflow A`",
                "`Auto-triage-job-name: Job B`",
                "`Auto-triage-fingerprint: fp-123`",
                "`Auto-triage-assignees-gh: [\"old-user\"]`",
                "`Auto-triage-assignees-slack: [\"UOLD\"]`",
                "`Auto-triage-assignee-source: pipeline_reorg`",
                "<!-- AUTO-TRIAGE-METADATA-END -->",
            ]
        )

        self.assertTrue(has_assignee_markers(original))
        cleaned = strip_assignee_markers(original)
        self.assertFalse(has_assignee_markers(cleaned))
        self.assertEqual(
            parse_base_markers(cleaned),
            {"workflow_name": "Workflow A", "job_name": "Job B", "fingerprint": "fp-123"},
        )
        self.assertNotIn("old-user", cleaned)
        self.assertNotIn("UOLD", cleaned)
        self.assertNotIn("pipeline_reorg", cleaned)
        self.assertIn("body text", cleaned)

    def test_strip_assignee_markers_removes_empty_sealed_block(self) -> None:
        body = "\n".join(
            [
                "just a body",
                "",
                "<!-- AUTO-TRIAGE-METADATA-START -->",
                "`Auto-triage-assignees-gh: []`",
                "`Auto-triage-assignees-slack: [\"UX\"]`",
                "`Auto-triage-assignee-source: agent`",
                "<!-- AUTO-TRIAGE-METADATA-END -->",
            ]
        )

        cleaned = strip_assignee_markers(body)
        self.assertFalse(has_assignee_markers(cleaned))
        self.assertNotIn("AUTO-TRIAGE-METADATA-START", cleaned)
        self.assertIn("just a body", cleaned)

    def test_strip_is_no_op_when_no_markers_present(self) -> None:
        body = "plain body with no sealed block"
        self.assertEqual(strip_assignee_markers(body), body)
        self.assertFalse(has_assignee_markers(body))


if __name__ == "__main__":
    unittest.main()
