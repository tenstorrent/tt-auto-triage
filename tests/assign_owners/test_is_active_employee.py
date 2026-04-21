import unittest
import urllib.error
from unittest.mock import patch

from tools.ci.assign_owners import __main__ as mod


class IsActiveEmployeeTests(unittest.TestCase):
    def setUp(self) -> None:
        mod._active_cache.clear()

    def _slack(self, sid: str, deleted: bool) -> dict:
        return {"id": sid, "deleted": deleted, "real_name": "X", "display_name": "x"}

    def test_both_live_returns_true(self) -> None:
        slack_dir = [self._slack("U1", deleted=False)]
        with patch("urllib.request.urlopen") as fake:
            fake.return_value.__enter__.return_value.status = 204
            self.assertTrue(mod.is_active_employee("U1", "alice", slack_dir, "tok"))

    def test_slack_deleted_alone_blocks(self) -> None:
        slack_dir = [self._slack("U1", deleted=True)]
        with patch("urllib.request.urlopen") as fake:
            fake.return_value.__enter__.return_value.status = 204  # GH still has them
            self.assertFalse(mod.is_active_employee("U1", "alice", slack_dir, "tok"))
            fake.assert_not_called()  # slack signal is decisive before we bother hitting GH

    def test_github_404_alone_blocks(self) -> None:
        slack_dir = [self._slack("U1", deleted=False)]
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
            "url", 404, "Not Found", hdrs=None, fp=None  # type: ignore[arg-type]
        )):
            self.assertFalse(mod.is_active_employee("U1", "alice", slack_dir, "tok"))

    def test_github_302_alone_blocks(self) -> None:
        slack_dir = [self._slack("U1", deleted=False)]
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
            "url", 302, "Found", hdrs=None, fp=None  # type: ignore[arg-type]
        )):
            self.assertFalse(mod.is_active_employee("U1", "alice", slack_dir, "tok"))

    def test_github_403_scope_issue_does_not_block(self) -> None:
        # Missing read:org scope -> 403. Must not be treated as "gone".
        slack_dir = [self._slack("U1", deleted=False)]
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
            "url", 403, "Forbidden", hdrs=None, fp=None  # type: ignore[arg-type]
        )):
            self.assertTrue(mod.is_active_employee("U1", "alice", slack_dir, "tok"))

    def test_missing_from_non_empty_slack_dump_blocks(self) -> None:
        # Pipeline_reorg points at U_GONE but the Slack dump does not know this ID at all.
        slack_dir = [self._slack("U1", deleted=False)]
        with patch("urllib.request.urlopen") as fake:
            fake.return_value.__enter__.return_value.status = 204
            self.assertFalse(mod.is_active_employee("U_GONE", "", slack_dir, "tok"))
            fake.assert_not_called()

    def test_missing_with_empty_slack_dump_does_not_block(self) -> None:
        # Empty dump means Slack lookup is unusable, not that the user is gone.
        with patch("urllib.request.urlopen") as fake:
            fake.return_value.__enter__.return_value.status = 204
            self.assertTrue(mod.is_active_employee("U_GONE", "alice", [], "tok"))

    def test_ex_employees_override_by_slack_id(self) -> None:
        slack_dir = [self._slack("UGONE", deleted=False)]
        with patch.object(mod, "EX_EMPLOYEES", frozenset({"UGONE"})):
            self.assertFalse(mod.is_active_employee("UGONE", "alice", slack_dir, "tok"))

    def test_ex_employees_override_by_github_login(self) -> None:
        slack_dir = [self._slack("U1", deleted=False)]
        with patch.object(mod, "EX_EMPLOYEES", frozenset({"alice-gh"})):
            self.assertFalse(mod.is_active_employee("U1", "alice-gh", slack_dir, "tok"))

    def test_caching_avoids_repeat_lookups(self) -> None:
        slack_dir = [self._slack("U1", deleted=False)]
        with patch("urllib.request.urlopen") as fake:
            fake.return_value.__enter__.return_value.status = 204
            self.assertTrue(mod.is_active_employee("U1", "alice", slack_dir, "tok"))
            self.assertTrue(mod.is_active_employee("U1", "alice", slack_dir, "tok"))
            self.assertEqual(fake.call_count, 1)

    def test_no_login_no_github_check_required(self) -> None:
        # Slack says active, no login to check GH with -> treat as active.
        slack_dir = [self._slack("U1", deleted=False)]
        with patch("urllib.request.urlopen") as fake:
            self.assertTrue(mod.is_active_employee("U1", "", slack_dir, "tok"))
            fake.assert_not_called()


if __name__ == "__main__":
    unittest.main()
