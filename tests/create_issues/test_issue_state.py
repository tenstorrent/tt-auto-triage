import unittest

from tools.ci.create_issues.helpers import sanitize_text as sanitize_issue_text
from tools.ci.create_issues.issue_state import append_base_markers, tracked_pairs_from_issues


class CreateIssuesIssueStateTests(unittest.TestCase):
    def test_append_base_markers_adds_contract_once(self) -> None:
        body = append_base_markers(
            "Issue body",
            workflow_name="Nightly Workflow",
            job_name="linux / unit",
        )

        self.assertIn("`Auto-triage-workflow: Nightly Workflow`", body)
        self.assertIn("`Auto-triage-job-name: linux / unit`", body)
        self.assertNotIn("fingerprint", body)

        body_again = append_base_markers(
            body,
            workflow_name="Nightly Workflow",
            job_name="linux / unit",
        )

        self.assertEqual(body, body_again)

    def test_tracked_pairs_from_issues_reads_existing_markers(self) -> None:
        issues = [
            {
                "body": "\n".join(
                    [
                        "something",
                        "<!-- AUTO-TRIAGE-METADATA-START -->",
                        "`Auto-triage-workflow: Workflow A`",
                        "`Auto-triage-job-name: Job A`",
                        "<!-- AUTO-TRIAGE-METADATA-END -->",
                    ]
                )
            },
            {
                "body": "\n".join(
                    [
                        "<!-- AUTO-TRIAGE-METADATA-START -->",
                        "`Auto-triage-workflow: Workflow B`",
                        "`Auto-triage-job-name: Job B`",
                        "<!-- AUTO-TRIAGE-METADATA-END -->",
                    ]
                )
            },
            {"body": "no markers here"},
        ]

        self.assertEqual(
            tracked_pairs_from_issues(issues),
            {("Workflow A", "Job A"), ("Workflow B", "Job B")},
        )

    def test_append_base_markers_with_extra_jobs(self) -> None:
        extra = [("Workflow C", "Job C"), ("Workflow D", "Job D")]
        body = append_base_markers(
            "Issue body",
            workflow_name="Workflow A",
            job_name="Job A",
            extra_jobs=extra,
        )

        self.assertIn("`Auto-triage-workflow: Workflow A`", body)
        self.assertIn("`Auto-triage-job-name: Job A`", body)
        self.assertIn("`Auto-triage-workflow: Workflow C`", body)
        self.assertIn("`Auto-triage-job-name: Job C`", body)
        self.assertIn("`Auto-triage-workflow: Workflow D`", body)
        self.assertIn("`Auto-triage-job-name: Job D`", body)

    def test_tracked_pairs_from_issues_recovers_all_extra_jobs(self) -> None:
        extra = [("Workflow C", "Job C"), ("Workflow D", "Job D")]
        issue_body = append_base_markers(
            "Issue body",
            workflow_name="Workflow A",
            job_name="Job A",
            extra_jobs=extra,
        )
        issues = [{"body": issue_body}]

        pairs = tracked_pairs_from_issues(issues)

        self.assertEqual(
            pairs,
            {("Workflow A", "Job A"), ("Workflow C", "Job C"), ("Workflow D", "Job D")},
        )

    def test_sanitize_issue_body_redacts_common_secrets(self) -> None:
        body = (
            "token ghp_123456789012345678901234567890123456\n"
            "Authorization: Bearer abc123\n"
            "api_key=super-secret\n"
            "xoxb-1234567890-1234567890-abcdef"
        )

        sanitized = sanitize_issue_text(body)

        self.assertNotIn("ghp_123456789012345678901234567890123456", sanitized)
        self.assertNotIn("Bearer abc123", sanitized)
        self.assertNotIn("super-secret", sanitized)
        self.assertNotIn("xoxb-1234567890-1234567890-abcdef", sanitized)
        self.assertIn("[REDACTED_GITHUB_TOKEN]", sanitized)
        self.assertIn("[REDACTED_TOKEN]", sanitized)
        self.assertIn("[REDACTED_KEY]", sanitized)
        self.assertIn("[REDACTED_SLACK_TOKEN]", sanitized)


if __name__ == "__main__":
    unittest.main()
