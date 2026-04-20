import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.ci.assign_owners.owners import resolve_owners


class AssignOwnersResolverTests(unittest.TestCase):
    def test_pipeline_reorg_has_highest_priority_and_returns_name(self) -> None:
        resolved = resolve_owners(
            workflow_name="(triage) Nightly",
            job_name="Galaxy Fabric unit tests",
            owners_json=[],
            pipeline_owners=[{"name": "Galaxy Fabric unit tests", "id": "U123", "owner_name": "Alice"}],
            codeowners={".github/workflows/triage-ci.yaml": ["owner1"]},
            slack_directory=[],
            github_token=None,
            repo_root=Path("."),
            git_history_max_commits=10,
        )

        self.assertEqual(resolved["source"], "pipeline_reorg")
        self.assertEqual(resolved["github_assignees"], [])
        self.assertEqual(resolved["slack_assignees"], ["U123"])
        self.assertEqual(resolved["slack_names"], ["Alice"])

    def test_owners_json_fills_slack_names_from_directory(self) -> None:
        resolved = resolve_owners(
            workflow_name="(triage) Nightly",
            job_name="galaxy-ci / integration / flake",
            owners_json=[{"job-name-component": "integration", "owner": {"id": "U999"}}],
            pipeline_owners=[],
            codeowners={".github/workflows/triage-ci.yaml": ["codeowner-user"]},
            slack_directory=[{"id": "U999", "real_name": "Betty Example"}],
            github_token=None,
            repo_root=Path("."),
            git_history_max_commits=10,
        )

        self.assertEqual(resolved["source"], "owners_json")
        self.assertEqual(resolved["slack_assignees"], ["U999"])
        self.assertEqual(resolved["slack_names"], ["Betty Example"])

    @patch("tools.ci.assign_owners.owners._resolve_github_users")
    @patch("tools.ci.assign_owners.owners._git_history_candidates")
    def test_codeowners_beats_git_history(self, mock_history, mock_resolve) -> None:
        mock_history.return_value = (["history-user"], [])
        mock_resolve.return_value = (["codeowner-user"], ["Codeowner User"], ["UCODEOWNER"], ["Codeowner User"])

        with tempfile.TemporaryDirectory() as repo:
            repo_root = Path(repo)
            (repo_root / ".github" / "workflows").mkdir(parents=True)
            (repo_root / ".github" / "workflows" / "my-example-workflow.yaml").write_text("name: x\n")

            resolved = resolve_owners(
                workflow_name="My Example Workflow",
                job_name="job-x",
                owners_json=[],
                pipeline_owners=[],
                codeowners={".github/workflows/my-example-workflow.yaml": ["codeowner-user"]},
                slack_directory=[],
                github_token="token",
                repo_root=repo_root,
                git_history_max_commits=10,
            )

        self.assertEqual(resolved["source"], "CODEOWNERS")
        self.assertEqual(resolved["github_assignees"], ["codeowner-user"])
        self.assertEqual(resolved["github_names"], ["Codeowner User"])
        mock_history.assert_not_called()

    def test_codeowners_matches_against_actual_workflow_file_on_disk(self) -> None:
        # Regression: CODEOWNERS resolution now uses the real workflow file path, not a fuzzy display-name match.
        with tempfile.TemporaryDirectory() as repo:
            repo_root = Path(repo)
            wf_dir = repo_root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "t3000-perf-tests.yaml").write_text("name: perf\n")

            with patch("tools.ci.assign_owners.owners._resolve_github_users") as mock_resolve:
                mock_resolve.return_value = (["t3k-owner"], ["T3K Owner"], [], [])
                resolved = resolve_owners(
                    workflow_name="(T3K) T3000 perf tests",
                    job_name="some-job",
                    owners_json=[],
                    pipeline_owners=[],
                    codeowners={".github/workflows/t3000-perf-tests.yaml": ["t3k-owner"]},
                    slack_directory=[],
                    github_token=None,
                    repo_root=repo_root,
                    git_history_max_commits=10,
                )

        self.assertEqual(resolved["source"], "CODEOWNERS")
        self.assertEqual(resolved["github_assignees"], ["t3k-owner"])

    @patch("tools.ci.assign_owners.owners._git_history_candidates")
    @patch("tools.ci.assign_owners.owners._github_user_info")
    def test_git_history_resolves_real_email_authors_via_slack(self, mock_info, mock_history) -> None:
        # Regression: non-noreply emails from git history should still match Slack profiles.
        mock_history.return_value = ([], ["evan@tenstorrent.com"])
        mock_info.return_value = {"name": "", "email": ""}

        slack_directory = [{"id": "UEVAN", "real_name": "Evan Example", "email": "evan@tenstorrent.com"}]
        resolved = resolve_owners(
            workflow_name="(triage) custom workflow",
            job_name="job-x",
            owners_json=[],
            pipeline_owners=[],
            codeowners={},
            slack_directory=slack_directory,
            github_token=None,
            repo_root=Path("."),
            git_history_max_commits=10,
        )

        self.assertEqual(resolved["source"], "git_history")
        self.assertEqual(resolved["slack_assignees"], ["UEVAN"])
        self.assertEqual(resolved["slack_names"], ["Evan Example"])

    @patch("tools.ci.assign_owners.owners._github_user_info")
    @patch("tools.ci.assign_owners.owners.api_get")
    def test_pipeline_reorg_reverse_resolves_github_via_slack_email(
        self, mock_api_get, mock_info
    ) -> None:
        mock_api_get.return_value = {"items": [{"login": "alice-gh"}]}
        mock_info.return_value = {"name": "Alice Example", "email": ""}

        resolved = resolve_owners(
            workflow_name="(triage) Nightly",
            job_name="Galaxy Fabric unit tests",
            owners_json=[],
            pipeline_owners=[{"name": "Galaxy Fabric unit tests", "id": "U123", "owner_name": "Alice Example"}],
            codeowners={},
            slack_directory=[{"id": "U123", "real_name": "Alice Example", "email": "alice@tenstorrent.com"}],
            github_token="token",
            repo_root=Path("."),
            git_history_max_commits=10,
        )

        self.assertEqual(resolved["source"], "pipeline_reorg")
        self.assertEqual(resolved["slack_assignees"], ["U123"])
        self.assertEqual(resolved["slack_names"], ["Alice Example"])
        self.assertEqual(resolved["github_assignees"], ["alice-gh"])
        self.assertEqual(resolved["github_names"], ["Alice Example"])
        # First call should be the email search.
        first_url = mock_api_get.call_args_list[0][0][0]
        self.assertIn("alice%40tenstorrent.com", first_url)
        self.assertIn("in%3Aemail", first_url)

    @patch("tools.ci.assign_owners.owners._github_user_info")
    @patch("tools.ci.assign_owners.owners.api_get")
    def test_pipeline_reorg_reverse_falls_back_to_name_search(
        self, mock_api_get, mock_info
    ) -> None:
        # First call (email search) returns nothing; second call (name search) returns a unique hit.
        mock_api_get.side_effect = [
            {"items": []},
            {"items": [{"login": "bob-gh"}]},
        ]
        mock_info.return_value = {"name": "Bob Example", "email": ""}

        resolved = resolve_owners(
            workflow_name="(triage) Nightly",
            job_name="Galaxy Fabric unit tests",
            owners_json=[],
            pipeline_owners=[{"name": "Galaxy Fabric unit tests", "id": "U777", "owner_name": "Bob Example"}],
            codeowners={},
            slack_directory=[{"id": "U777", "real_name": "Bob Example", "email": "bob@example.com"}],
            github_token="token",
            repo_root=Path("."),
            git_history_max_commits=10,
        )

        self.assertEqual(resolved["github_assignees"], ["bob-gh"])
        self.assertEqual(resolved["github_names"], ["Bob Example"])
        self.assertEqual(mock_api_get.call_count, 2)
        second_url = mock_api_get.call_args_list[1][0][0]
        self.assertIn("in%3Afullname", second_url)
        self.assertIn("Bob%20Example", second_url)

    @patch("tools.ci.assign_owners.owners._github_user_info")
    @patch("tools.ci.assign_owners.owners.api_get")
    def test_pipeline_reorg_reverse_disambiguates_via_known_handles(
        self, mock_api_get, mock_info
    ) -> None:
        # Search returns 3 candidates. "samueltt" matches known handle "samuel"
        # (e.g. from branch prefixes like "samuel/feature-x" on the target repo).
        mock_api_get.return_value = {"items": [
            {"login": "someone-else"},
            {"login": "samueltt"},
            {"login": "third-party"},
        ]}
        mock_info.return_value = {"name": "Samuel Example", "email": ""}

        commit_identity_index = {
            "by_name": {},
            "by_email": {},
            "handles": {"samuel"},
        }

        resolved = resolve_owners(
            workflow_name="(triage) Nightly",
            job_name="some-job",
            owners_json=[],
            pipeline_owners=[{"name": "some-job", "id": "U999", "owner_name": "Samuel Example"}],
            codeowners={},
            slack_directory=[{"id": "U999", "real_name": "Samuel Example", "email": "s@e.com"}],
            github_token="token",
            repo_root=Path("."),
            git_history_max_commits=10,
            commit_identity_index=commit_identity_index,
        )

        self.assertEqual(resolved["github_assignees"], ["samueltt"])
        self.assertEqual(resolved["github_names"], ["Samuel Example"])

    def test_pipeline_reorg_reverse_uses_commit_identity_index(self) -> None:
        # Surefire path: a dev who has committed to the target repo is looked up
        # offline via the commit identity index — no GitHub API calls required.
        commit_identity_index = {
            "by_name": {
                "alicedev": [{"login": "alice-gh", "name": "Alice Dev", "email": "1+alice-gh@users.noreply.github.com"}],
            },
            "by_email": {
                "1+alice-gh@users.noreply.github.com": [{"login": "alice-gh", "name": "Alice Dev", "email": "1+alice-gh@users.noreply.github.com"}],
            },
            "handles": {"alice-gh"},
        }

        resolved = resolve_owners(
            workflow_name="(triage) Nightly",
            job_name="Galaxy Fabric unit tests",
            owners_json=[],
            pipeline_owners=[{"name": "Galaxy Fabric unit tests", "id": "U123", "owner_name": "Alice Dev"}],
            codeowners={},
            slack_directory=[{"id": "U123", "real_name": "Alice Dev", "email": "alice@corp.com"}],
            github_token=None,  # no GitHub token required when index hits
            repo_root=Path("."),
            git_history_max_commits=10,
            commit_identity_index=commit_identity_index,
        )

        self.assertEqual(resolved["source"], "pipeline_reorg")
        self.assertEqual(resolved["github_assignees"], ["alice-gh"])
        self.assertEqual(resolved["github_names"], ["Alice Dev"])
        self.assertEqual(resolved["slack_assignees"], ["U123"])
        self.assertEqual(resolved["slack_names"], ["Alice Dev"])

    @patch("tools.ci.assign_owners.owners._github_user_info")
    @patch("tools.ci.assign_owners.owners.api_get")
    def test_pipeline_reorg_reverse_ambiguous_match_is_dropped(
        self, mock_api_get, mock_info
    ) -> None:
        mock_api_get.return_value = {"items": [{"login": "a"}, {"login": "b"}]}
        mock_info.return_value = {"name": "", "email": ""}

        resolved = resolve_owners(
            workflow_name="(triage) Nightly",
            job_name="Galaxy Fabric unit tests",
            owners_json=[],
            pipeline_owners=[{"name": "Galaxy Fabric unit tests", "id": "U123", "owner_name": "Common Name"}],
            codeowners={},
            slack_directory=[{"id": "U123", "real_name": "Common Name", "email": "c@c.com"}],
            github_token="token",
            repo_root=Path("."),
            git_history_max_commits=10,
        )

        self.assertEqual(resolved["github_assignees"], [])
        self.assertEqual(resolved["slack_assignees"], ["U123"])

    @patch("tools.ci.assign_owners.owners._git_history_candidates")
    def test_none_when_no_sources_match(self, mock_history) -> None:
        mock_history.return_value = ([], [])

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

        self.assertEqual(resolved["source"], "none")
        self.assertEqual(resolved["github_assignees"], [])
        self.assertEqual(resolved["slack_assignees"], [])


if __name__ == "__main__":
    unittest.main()
