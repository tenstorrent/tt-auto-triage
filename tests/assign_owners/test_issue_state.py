import unittest

from tools.ci.assign_owners.issue_state import (
    parse_base_markers,
    parse_assignee_markers,
    upsert_assignee_markers,
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
                "suggested_owners": [],
            },
        )

    def test_parse_base_markers_reads_suggested_owners(self) -> None:
        body = "\n".join(
            [
                "<!-- AUTO-TRIAGE-METADATA-START -->",
                "`Auto-triage-workflow: Workflow A`",
                "`Auto-triage-job-name: Job B`",
                '`Auto-triage-suggested-owners: ["alice","bob"]`',
                "<!-- AUTO-TRIAGE-METADATA-END -->",
            ]
        )

        result = parse_base_markers(body)
        self.assertEqual(result["suggested_owners"], ["alice", "bob"])

    def test_upsert_assignee_markers_replaces_existing_values(self) -> None:
        original = "\n".join(
            [
                "body",
                "<!-- AUTO-TRIAGE-METADATA-START -->",
                "`Auto-triage-workflow: Workflow A`",
                "`Auto-triage-assignees-gh: [\"old-user\"]`",
                "`Auto-triage-assignees-slack: [\"UOLD\"]`",
                "`Auto-triage-assignee-source: old-source`",
                "<!-- AUTO-TRIAGE-METADATA-END -->",
            ]
        )

        updated = upsert_assignee_markers(
            original,
            github_assignees=["alice", "bob"],
            slack_assignees=["UALICE"],
            source="CODEOWNERS",
        )

        self.assertEqual(
            parse_assignee_markers(updated),
            {
                "github_assignees": ["alice", "bob"],
                "slack_assignees": ["UALICE"],
                "source": "CODEOWNERS",
            },
        )
        self.assertNotIn("old-user", updated)
        self.assertNotIn("UOLD", updated)


if __name__ == "__main__":
    unittest.main()
