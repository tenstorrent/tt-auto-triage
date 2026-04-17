import unittest
from pathlib import Path
from unittest.mock import patch

from tools.ci.assign_owners.owners import resolve_owners


class AssignOwnersResolverTests(unittest.TestCase):
    def test_pipeline_reorg_has_highest_priority(self) -> None:
        resolved = resolve_owners(
            workflow_name="(triage) Nightly",
            job_name="Galaxy Fabric unit tests",
            owners_json=[],
            pipeline_owners=[{"name": "Galaxy Fabric unit tests", "id": "U123", "owner_name": "Owner"}],
            codeowners={".github/workflows/triage-ci.yaml": ["owner1"]},
            slack_directory=[],
            github_token=None,
            repo_root=Path("."),
            git_history_max_commits=10,
        )

        self.assertEqual(
            resolved,
            {
                "source": "pipeline_reorg",
                "github_assignees": [],
                "slack_assignees": ["U123"],
            },
        )

    def test_owners_json_used_before_codeowners_and_git_history(self) -> None:
        resolved = resolve_owners(
            workflow_name="(triage) Nightly",
            job_name="galaxy-ci / integration / flake",
            owners_json=[
                {
                    "job-name-component": "integration",
                    "owner": {"id": "U999"},
                }
            ],
            pipeline_owners=[],
            codeowners={".github/workflows/triage-ci.yaml": ["codeowner-user"]},
            slack_directory=[],
            github_token=None,
            repo_root=Path("."),
            git_history_max_commits=10,
        )

        self.assertEqual(
            resolved,
            {
                "source": "owners_json",
                "github_assignees": [],
                "slack_assignees": ["U999"],
            },
        )

    @patch("tools.ci.assign_owners.owners._resolve_github_users")
    @patch("tools.ci.assign_owners.owners._github_users_from_git_history")
    def test_codeowners_beats_git_history(self, mock_git_history, mock_resolve_github_users) -> None:
        mock_git_history.return_value = ["history-user"]
        mock_resolve_github_users.return_value = (["codeowner-user"], ["UCODEOWNER"])

        resolved = resolve_owners(
            workflow_name="(triage) ci",
            job_name="job-x",
            owners_json=[],
            pipeline_owners=[],
            codeowners={".github/workflows/triage-ci.yaml": ["codeowner-user"]},
            slack_directory=[],
            github_token="token",
            repo_root=Path("."),
            git_history_max_commits=10,
        )

        self.assertEqual(resolved["source"], "CODEOWNERS")
        self.assertEqual(resolved["github_assignees"], ["codeowner-user"])
        mock_git_history.assert_not_called()

    @patch("tools.ci.assign_owners.owners._resolve_github_users")
    @patch("tools.ci.assign_owners.owners._github_users_from_git_history")
    def test_git_history_used_as_last_fallback(self, mock_git_history, mock_resolve_github_users) -> None:
        mock_git_history.return_value = ["history-user"]
        mock_resolve_github_users.return_value = (["history-user"], ["UHISTORY"])

        resolved = resolve_owners(
            workflow_name="(triage) custom workflow",
            job_name="job-x",
            owners_json=[],
            pipeline_owners=[],
            codeowners={},
            slack_directory=[],
            github_token="token",
            repo_root=Path("."),
            git_history_max_commits=10,
        )

        self.assertEqual(
            resolved,
            {
                "source": "git_history",
                "github_assignees": ["history-user"],
                "slack_assignees": ["UHISTORY"],
            },
        )
        mock_git_history.assert_called_once()

    @patch("tools.ci.assign_owners.owners._github_users_from_git_history")
    def test_none_when_no_sources_match(self, mock_git_history) -> None:
        mock_git_history.return_value = []

        resolved = resolve_owners(
            workflow_name="(triage) unknown",
            job_name="unknown job",
            owners_json=[],
            pipeline_owners=[],
            codeowners={},
            slack_directory=[],
            github_token=None,
            repo_root=Path("."),
            git_history_max_commits=10,
        )

        self.assertEqual(
            resolved,
            {
                "source": "none",
                "github_assignees": [],
                "slack_assignees": [],
            },
        )


if __name__ == "__main__":
    unittest.main()
