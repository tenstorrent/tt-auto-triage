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
        self.pipeline = {
            "Job Alpha": {"name": "Job Alpha", "id": "U1", "owner_name": "Alice A."},
            "Job Beta": {"name": "Job Beta", "id": "U2", "owner_name": "Old Oleg"},
        }

    def test_fast_path_pipeline_reorg_hit_for_active_employee(self) -> None:
        with patch.object(mod, "_resolve_via_agent") as agent:
            r = mod.resolve_owner("WF", "Job Alpha", self.pipeline, self.slack_dir)
        agent.assert_not_called()
        self.assertEqual(r["source"], "pipeline_reorg")
        self.assertEqual(r["slack_assignees"], ["U1"])
        self.assertEqual(r["slack_names"], ["Alice A."])
        self.assertEqual(r["github_assignees"], [])

    def test_fast_path_enriches_from_identity_index(self) -> None:
        identity = {"U1": {"github_login": "alice-gh", "github_name": "Alice A."}}
        with patch.object(mod, "_resolve_via_agent") as agent:
            r = mod.resolve_owner("WF", "Job Alpha", self.pipeline, self.slack_dir, identity)
        agent.assert_not_called()
        self.assertEqual(r["source"], "pipeline_reorg")
        self.assertEqual(r["github_assignees"], ["alice-gh"])
        self.assertEqual(r["github_names"], ["Alice A."])
        self.assertEqual(r["slack_assignees"], ["U1"])

    def test_slow_path_when_pipeline_owner_left_includes_ex_owner_note(self) -> None:
        captured: dict = {}

        def fake_agent(wf: str, job: str, note: str, slack_dir=None,
                       extra_ex=frozenset(), extra_context: str = "") -> dict:
            captured["note"] = note
            return {"source": "agent", "github_assignees": ["replace"],
                    "github_names": ["Replace R."], "slack_assignees": ["UNEW"], "slack_names": ["New N."]}

        # Job Beta -> U2, whose Slack entry has deleted=True. Under the tightened rule,
        # Slack-deleted alone is enough to fail is_active_employee and fall through to the agent.
        with patch.object(mod, "_resolve_via_agent", side_effect=fake_agent):
            r = mod.resolve_owner("WF", "Job Beta", self.pipeline, self.slack_dir)
        self.assertEqual(r["source"], "agent")
        self.assertIn("Old Oleg", captured["note"])
        self.assertIn("U2", captured["note"])
        self.assertEqual(r["slack_assignees"], ["UNEW"])

    def test_slow_path_when_pipeline_has_no_entry_passes_empty_note(self) -> None:
        captured: dict = {}

        def fake_agent(wf: str, job: str, note: str, slack_dir=None,
                       extra_ex=frozenset(), extra_context: str = "") -> dict:
            captured["note"] = note
            return {"source": "agent", "github_assignees": ["picked"],
                    "github_names": ["P."], "slack_assignees": ["UPK"], "slack_names": ["P."]}

        with patch.object(mod, "_resolve_via_agent", side_effect=fake_agent):
            r = mod.resolve_owner("WF", "Unknown Job", self.pipeline, self.slack_dir)
        self.assertEqual(captured["note"], "")
        self.assertEqual(r["source"], "agent")

    def test_ex_employees_override_forces_slow_path_even_when_slack_says_active(self) -> None:
        captured: dict = {}

        def fake_agent(wf: str, job: str, note: str, slack_dir=None,
                       extra_ex=frozenset(), extra_context: str = "") -> dict:
            captured["note"] = note
            return {"source": "agent", "github_assignees": ["picked"], "github_names": ["P."],
                    "slack_assignees": ["UPK"], "slack_names": ["P."]}

        # U1 is NOT deleted in Slack, but we put them on the ex-employees list anyway
        # (mirrors the real-world Salar case where Slack admin hasn't deactivated yet).
        with patch.object(mod, "EX_EMPLOYEES", frozenset({"U1"})), \
             patch.object(mod, "_resolve_via_agent", side_effect=fake_agent):
            r = mod.resolve_owner("WF", "Job Alpha", self.pipeline, self.slack_dir)
        self.assertEqual(r["source"], "agent")
        self.assertIn("Alice A.", captured["note"])
        self.assertIn("U1", captured["note"])

    def test_agent_none_response_propagates(self) -> None:
        empty = {"source": "none", "github_assignees": [], "github_names": [],
                 "slack_assignees": [], "slack_names": []}
        with patch.object(mod, "_resolve_via_agent", return_value=empty):
            r = mod.resolve_owner("WF", "Unknown Job", self.pipeline, self.slack_dir)
        self.assertEqual(r["source"], "none")


if __name__ == "__main__":
    unittest.main()
