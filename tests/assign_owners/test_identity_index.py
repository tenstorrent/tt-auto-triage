import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.ci.assign_owners import identity


def _proc(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class IdentityIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.slack_dir = [
            {"id": "U1", "real_name": "Alice Anderson", "display_name": "alice", "is_bot": False, "deleted": False},
            {"id": "U2", "real_name": "Bob Bobby", "display_name": "bob", "is_bot": False, "deleted": False},
            {"id": "UBOT", "real_name": "BotBot", "is_bot": True, "deleted": False},
            {"id": "UGONE", "real_name": "Alice Anderson", "is_bot": False, "deleted": True},
        ]

    def test_noreply_email_extracts_login_and_matches_slack_real_name(self) -> None:
        log = "\n".join([
            "Alice Anderson\t12345+alice-gh@users.noreply.github.com",
            "Bob Bobby\tbob@personal.example",
            "Alice Anderson\t12345+alice-gh@users.noreply.github.com",
        ])
        with patch("subprocess.run", return_value=_proc(log)), \
             patch.object(Path, "exists", return_value=True):
            idx = identity.build_identity_index(Path("/fake/repo"), self.slack_dir)
        self.assertEqual(idx.get("U1"), {"github_login": "alice-gh", "github_name": "Alice Anderson"})
        self.assertNotIn("U2", idx)
        self.assertNotIn("UBOT", idx)
        self.assertNotIn("UGONE", idx)

    def test_display_name_fallback_matches(self) -> None:
        log = "alice\t99+alice-gh@users.noreply.github.com"
        with patch("subprocess.run", return_value=_proc(log)), \
             patch.object(Path, "exists", return_value=True):
            idx = identity.build_identity_index(Path("/fake/repo"), self.slack_dir)
        self.assertEqual(idx.get("U1", {}).get("github_login"), "alice-gh")

    def test_bot_logins_are_filtered(self) -> None:
        log = "\n".join([
            "dependabot[bot]\t49699333+dependabot[bot]@users.noreply.github.com",
            "github-actions\t41898282+github-actions@users.noreply.github.com",
        ])
        with patch("subprocess.run", return_value=_proc(log)), \
             patch.object(Path, "exists", return_value=True):
            idx = identity.build_identity_index(Path("/fake/repo"), self.slack_dir)
        self.assertEqual(idx, {})

    def test_missing_repo_returns_empty_index(self) -> None:
        with patch.object(Path, "exists", return_value=False):
            idx = identity.build_identity_index(Path("/does/not/exist"), self.slack_dir)
        self.assertEqual(idx, {})

    def test_git_failure_returns_empty_index(self) -> None:
        with patch("subprocess.run", return_value=_proc("", returncode=128)), \
             patch.object(Path, "exists", return_value=True):
            idx = identity.build_identity_index(Path("/fake/repo"), self.slack_dir)
        self.assertEqual(idx, {})


if __name__ == "__main__":
    unittest.main()
