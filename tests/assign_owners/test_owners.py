import unittest
from unittest.mock import patch

from tools.ci.assign_owners.owners import _is_slack_user_id, resolve_owners


class IsSlackUserIdTests(unittest.TestCase):
    def test_user_id(self) -> None:
        self.assertTrue(_is_slack_user_id("U01ABCDEF"))

    def test_enterprise_user_id(self) -> None:
        self.assertTrue(_is_slack_user_id("W01ABCDEF"))

    def test_usergroup_id(self) -> None:
        self.assertFalse(_is_slack_user_id("S01ABCDEF"))

    def test_bot_id(self) -> None:
        self.assertFalse(_is_slack_user_id("B01ABCDEF"))

    def test_empty(self) -> None:
        self.assertFalse(_is_slack_user_id(""))


class ResolveOwnersGroupFilterTests(unittest.TestCase):
    """Verify that group/usergroup Slack IDs are filtered out and the
    resolver falls through to CODEOWNERS for individual owners."""

    def test_pipeline_reorg_group_id_falls_through_to_codeowners(self) -> None:
        pipeline_owners = [{"name": "ttnn-unit", "id": "S_GROUP_1", "owner_name": "TTNN Core"}]
        codeowners = {".github/workflows/ttnn-unit.yaml": ["alice"]}

        with patch("tools.ci.assign_owners.owners._resolve_github_users") as mock_resolve:
            mock_resolve.return_value = (["alice"], ["UALICE"])
            result = resolve_owners(
                workflow_name="ttnn-unit",
                job_name="ttnn-unit",
                owners_json=[],
                pipeline_owners=pipeline_owners,
                codeowners=codeowners,
                slack_directory=[],
                github_token=None,
            )

        self.assertEqual(result["source"], "CODEOWNERS")
        self.assertEqual(result["github_assignees"], ["alice"])
        self.assertEqual(result["slack_assignees"], ["UALICE"])

    def test_pipeline_reorg_user_id_still_works(self) -> None:
        pipeline_owners = [{"name": "ttnn-unit", "id": "U_ALICE", "owner_name": "Alice"}]

        result = resolve_owners(
            workflow_name="ttnn-unit",
            job_name="ttnn-unit",
            owners_json=[],
            pipeline_owners=pipeline_owners,
            codeowners={},
            slack_directory=[],
            github_token=None,
        )

        self.assertEqual(result["source"], "pipeline_reorg")
        self.assertEqual(result["slack_assignees"], ["U_ALICE"])

    def test_owners_json_group_id_falls_through_to_codeowners(self) -> None:
        owners_json = [
            {"job-name-component": "ttnn-unit", "owner": {"id": "S_GROUP_2", "name": "Convolutions"}}
        ]
        codeowners = {".github/workflows/ttnn-unit.yaml": ["bob"]}

        with patch("tools.ci.assign_owners.owners._resolve_github_users") as mock_resolve:
            mock_resolve.return_value = (["bob"], ["UBOB"])
            result = resolve_owners(
                workflow_name="ttnn-unit",
                job_name="ttnn-unit",
                owners_json=owners_json,
                pipeline_owners=[],
                codeowners=codeowners,
                slack_directory=[],
                github_token=None,
            )

        self.assertEqual(result["source"], "CODEOWNERS")
        self.assertEqual(result["github_assignees"], ["bob"])
        self.assertEqual(result["slack_assignees"], ["UBOB"])

    def test_owners_json_mixed_ids_keeps_only_users(self) -> None:
        owners_json = [
            {
                "job-name-component": "ttnn-unit",
                "owner": [
                    {"id": "S_GROUP_3", "name": "Metal Infra"},
                    {"id": "U_CHARLIE", "name": "Charlie"},
                ],
            }
        ]

        result = resolve_owners(
            workflow_name="ttnn-unit",
            job_name="ttnn-unit",
            owners_json=owners_json,
            pipeline_owners=[],
            codeowners={},
            slack_directory=[],
            github_token=None,
        )

        self.assertEqual(result["source"], "owners_json")
        self.assertEqual(result["slack_assignees"], ["U_CHARLIE"])

    def test_all_group_ids_falls_through_to_none(self) -> None:
        pipeline_owners = [{"name": "ttnn-unit", "id": "S_GROUP_1", "owner_name": "TTNN Core"}]
        owners_json = [
            {"job-name-component": "ttnn-unit", "owner": {"id": "S_GROUP_2", "name": "Convolutions"}}
        ]

        result = resolve_owners(
            workflow_name="ttnn-unit",
            job_name="ttnn-unit",
            owners_json=owners_json,
            pipeline_owners=pipeline_owners,
            codeowners={},
            slack_directory=[],
            github_token=None,
        )

        self.assertEqual(result["source"], "none")
        self.assertEqual(result["slack_assignees"], [])


if __name__ == "__main__":
    unittest.main()
