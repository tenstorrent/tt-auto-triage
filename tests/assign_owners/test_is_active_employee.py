import unittest
from unittest.mock import patch

from tools.ci.assign_owners import __main__ as mod


class IsActiveEmployeeTests(unittest.TestCase):
    def setUp(self) -> None:
        mod._active_cache.clear()

    def _slack(self, sid: str, deleted: bool) -> dict:
        return {"id": sid, "deleted": deleted, "real_name": "X", "display_name": "x"}

    def test_both_live_returns_true(self) -> None:
        slack_dir = [self._slack("U1", deleted=False)]
        with patch.object(mod, "_is_org_member_ok", return_value=True, create=True):
            with patch("urllib.request.urlopen") as fake:
                fake.return_value.__enter__.return_value.status = 204
                self.assertTrue(mod.is_active_employee("U1", "alice", slack_dir, "tok"))

    def test_slack_deleted_only_returns_true_github_wins(self) -> None:
        # Slack says gone, but GitHub still has them -> keep as active (single-signal does not block).
        slack_dir = [self._slack("U1", deleted=True)]
        with patch("urllib.request.urlopen") as fake:
            fake.return_value.__enter__.return_value.status = 204
            self.assertTrue(mod.is_active_employee("U1", "alice", slack_dir, "tok"))

    def test_github_missing_only_returns_true_slack_wins(self) -> None:
        # GitHub says gone (404), but Slack still has them -> keep as active.
        import urllib.error
        slack_dir = [self._slack("U1", deleted=False)]
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
            "url", 404, "Not Found", hdrs=None, fp=None  # type: ignore[arg-type]
        )):
            self.assertTrue(mod.is_active_employee("U1", "alice", slack_dir, "tok"))

    def test_both_gone_returns_false(self) -> None:
        import urllib.error
        slack_dir = [self._slack("U1", deleted=True)]
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
            "url", 404, "Not Found", hdrs=None, fp=None  # type: ignore[arg-type]
        )):
            self.assertFalse(mod.is_active_employee("U1", "alice", slack_dir, "tok"))

    def test_caching_avoids_repeat_lookups(self) -> None:
        slack_dir = [self._slack("U1", deleted=False)]
        with patch("urllib.request.urlopen") as fake:
            fake.return_value.__enter__.return_value.status = 204
            self.assertTrue(mod.is_active_employee("U1", "alice", slack_dir, "tok"))
            self.assertTrue(mod.is_active_employee("U1", "alice", slack_dir, "tok"))
            self.assertEqual(fake.call_count, 1)

    def test_no_login_skips_github_check(self) -> None:
        # is_active_employee("U1", "", ...) -> no GH call, only Slack signal.
        # Slack-deleted-only should NOT be enough to block.
        slack_dir = [self._slack("U1", deleted=True)]
        with patch("urllib.request.urlopen") as fake:
            self.assertTrue(mod.is_active_employee("U1", "", slack_dir, "tok"))
            fake.assert_not_called()


if __name__ == "__main__":
    unittest.main()
