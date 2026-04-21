import unittest
from unittest.mock import patch

from tools.ci.assign_owners import __main__ as mod


class IsActiveEmployeeTests(unittest.TestCase):
    def setUp(self) -> None:
        mod._active_cache.clear()

    def _slack(self, sid: str, deleted: bool) -> dict:
        return {"id": sid, "deleted": deleted, "real_name": "X", "display_name": "x"}

    def test_active_when_slack_present_and_not_deleted(self) -> None:
        self.assertTrue(mod.is_active_employee("U1", "alice", [self._slack("U1", False)], "tok"))

    def test_slack_deleted_alone_blocks(self) -> None:
        self.assertFalse(mod.is_active_employee("U1", "alice", [self._slack("U1", True)], "tok"))

    def test_missing_from_non_empty_slack_dump_blocks(self) -> None:
        # Pipeline_reorg (or the agent) points at U_GONE but the Slack dump does not know
        # this ID at all -> they have been removed from the Slack workspace entirely.
        self.assertFalse(mod.is_active_employee("U_GONE", "", [self._slack("U1", False)], "tok"))

    def test_missing_with_empty_slack_dump_does_not_block(self) -> None:
        # Empty dump means Slack lookup is unusable, not that the user is gone.
        self.assertTrue(mod.is_active_employee("U_GONE", "alice", [], "tok"))

    def test_ex_employees_override_by_slack_id(self) -> None:
        slack_dir = [self._slack("UGONE", deleted=False)]
        with patch.object(mod, "EX_EMPLOYEES", frozenset({"UGONE"})):
            self.assertFalse(mod.is_active_employee("UGONE", "alice", slack_dir, "tok"))

    def test_ex_employees_override_by_github_login(self) -> None:
        slack_dir = [self._slack("U1", deleted=False)]
        with patch.object(mod, "EX_EMPLOYEES", frozenset({"alice-gh"})):
            self.assertFalse(mod.is_active_employee("U1", "alice-gh", slack_dir, "tok"))

    def test_github_org_is_never_consulted(self) -> None:
        # We must not hit /orgs/{org}/members because the token lacks read:org and
        # the endpoint would 404 real employees. Guard against future regressions.
        slack_dir = [self._slack("U1", deleted=False)]
        with patch("urllib.request.urlopen") as fake:
            self.assertTrue(mod.is_active_employee("U1", "sadesoyeTT", slack_dir, "tok"))
            fake.assert_not_called()

    def test_caching_is_stable(self) -> None:
        slack_dir = [self._slack("U1", deleted=False)]
        r1 = mod.is_active_employee("U1", "alice", slack_dir, "tok")
        r2 = mod.is_active_employee("U1", "alice", slack_dir, "tok")
        self.assertEqual(r1, r2)
        self.assertIn(("U1", "alice"), mod._active_cache)


if __name__ == "__main__":
    unittest.main()
