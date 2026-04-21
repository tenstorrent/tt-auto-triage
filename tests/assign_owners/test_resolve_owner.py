import unittest
from unittest.mock import patch

from tools.ci.assign_owners import __main__ as mod


class ResolveOwnerTests(unittest.TestCase):
    def setUp(self) -> None:
        mod._active_cache.clear()
        self.slack_dir = [
            {"id": "U1", "deleted": False, "real_name": "Alice A.", "display_name": "alice"},
            {"id": "U2", "deleted": True, "real_name": "Old Oleg", "display_name": "oleg"},
        ]
        self.pipeline = [
            {"name": "Job Alpha", "id": "U1", "owner_name": "Alice A."},
            {"name": "Job Beta", "id": "U2", "owner_name": "Old Oleg"},
        ]

    def test_fast_path_pipeline_reorg_hit_for_active_employee(self) -> None:
        with patch.object(mod, "_resolve_via_agent") as agent:
            r = mod.resolve_owner("WF", "Job Alpha", self.pipeline, self.slack_dir, None)
        agent.assert_not_called()
        self.assertEqual(r["source"], "pipeline_reorg")
        self.assertEqual(r["slack_assignees"], ["U1"])
        self.assertEqual(r["slack_names"], ["Alice A."])
        self.assertEqual(r["github_assignees"], [])

    def test_slow_path_when_pipeline_owner_left_includes_ex_owner_note(self) -> None:
        captured: dict = {}

        def fake_agent(wf: str, job: str, note: str) -> dict:
            captured["note"] = note
            return {"source": "agent", "github_assignees": ["replace"],
                    "github_names": ["Replace R."], "slack_assignees": ["UNEW"], "slack_names": ["New N."]}

        # Force Slack-deleted AND GitHub-missing -> ex-employee.
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
            "url", 404, "Not Found", hdrs=None, fp=None  # type: ignore[arg-type]
        )):
            with patch.object(mod, "_resolve_via_agent", side_effect=fake_agent):
                # Need GH login argument in is_active_employee — but resolve_owner passes "" for login.
                # So in the slow path we rely on Slack-deleted alone; but is_active_employee blocks
                # only when BOTH signals agree. With login="" the GH signal is skipped (treated
                # as "present"), so Slack-only would NOT trigger slow path. Patch _active_cache
                # directly to simulate a confirmed ex-employee.
                mod._active_cache[("U2", "")] = False
                r = mod.resolve_owner("WF", "Job Beta", self.pipeline, self.slack_dir, "tok")
        self.assertEqual(r["source"], "agent")
        self.assertIn("Old Oleg", captured["note"])
        self.assertIn("U2", captured["note"])
        self.assertEqual(r["slack_assignees"], ["UNEW"])

    def test_slow_path_when_pipeline_has_no_entry_passes_empty_note(self) -> None:
        captured: dict = {}

        def fake_agent(wf: str, job: str, note: str) -> dict:
            captured["note"] = note
            return {"source": "agent", "github_assignees": ["picked"],
                    "github_names": ["P."], "slack_assignees": ["UPK"], "slack_names": ["P."]}

        with patch.object(mod, "_resolve_via_agent", side_effect=fake_agent):
            r = mod.resolve_owner("WF", "Unknown Job", self.pipeline, self.slack_dir, "tok")
        self.assertEqual(captured["note"], "")
        self.assertEqual(r["source"], "agent")

    def test_agent_none_response_propagates(self) -> None:
        empty = {"source": "none", "github_assignees": [], "github_names": [],
                 "slack_assignees": [], "slack_names": []}
        with patch.object(mod, "_resolve_via_agent", return_value=empty):
            r = mod.resolve_owner("WF", "Unknown Job", self.pipeline, self.slack_dir, None)
        self.assertEqual(r["source"], "none")


if __name__ == "__main__":
    unittest.main()
