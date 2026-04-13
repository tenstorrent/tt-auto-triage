import unittest

from tools.ci.slack_notify.__main__ import format_owner_text


class FormatOwnerTextGroupFilterTests(unittest.TestCase):
    def test_user_ids_are_pinged(self) -> None:
        markers = {"slack_assignees": ["UALICE", "WBOB"], "github_assignees": [], "source": "owners_json"}
        result = format_owner_text(markers)
        self.assertIn("<@UALICE>", result)
        self.assertIn("<@WBOB>", result)

    def test_group_ids_are_excluded(self) -> None:
        markers = {"slack_assignees": ["S_GROUP_1"], "github_assignees": ["alice"], "source": "owners_json"}
        result = format_owner_text(markers)
        self.assertNotIn("<@S_GROUP_1>", result)
        self.assertIn("`@alice`", result)

    def test_all_group_ids_returns_no_assignees(self) -> None:
        markers = {"slack_assignees": ["S_GROUP_1", "S_GROUP_2"], "github_assignees": [], "source": "owners_json"}
        result = format_owner_text(markers)
        self.assertEqual(result, "No assignees available")

    def test_mixed_ids_keeps_only_users(self) -> None:
        markers = {"slack_assignees": ["S_GROUP_1", "UALICE"], "github_assignees": [], "source": "owners_json"}
        result = format_owner_text(markers)
        self.assertNotIn("S_GROUP_1", result)
        self.assertIn("<@UALICE>", result)


if __name__ == "__main__":
    unittest.main()
