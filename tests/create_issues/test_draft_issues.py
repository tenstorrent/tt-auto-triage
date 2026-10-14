import json
import subprocess
import unittest
from unittest.mock import patch

from tools.ci.create_issues import draft_issues


class DraftIssuesPromptAlignmentTests(unittest.TestCase):
    def test_uses_run_log_entries_to_preserve_run_indices(self) -> None:
        job = {
            "workflow_name": "wf",
            "job_name": "job",
            "job_urls": ["", "https://example.com/job/2", "https://example.com/job/3"],
            "run_urls": [],
            "run_log_entries": [
                {
                    "run_index": 2,
                    "job_url": "https://example.com/job/2",
                    "log_path": "/tmp/run2.log",
                },
                {
                    "run_index": 3,
                    "job_url": "https://example.com/job/3",
                    "log_path": "/tmp/run3.log",
                },
            ],
        }
        captured_prompt: dict[str, str] = {}

        def _fake_agent(prompt: str) -> str:
            captured_prompt["text"] = prompt
            payload = {
                "deterministic": True,
                "confidence": "high",
                "issue_title": "x",
                "issue_body": "y",
            }
            return f"{draft_issues.MARKER}\n{json.dumps(payload)}"

        with patch(
            "tools.ci.create_issues.draft_issues._run_llm_agent",
            side_effect=_fake_agent,
        ):
            result = draft_issues.draft_issue_body(job, ["/tmp/run2.log", "/tmp/run3.log"], consecutive=2)

        self.assertIsNotNone(result)
        prompt = captured_prompt["text"]
        self.assertIn("Run 2 job URL: https://example.com/job/2", prompt)
        self.assertIn("Run 2 local log path: /tmp/run2.log", prompt)
        self.assertIn("Run 3 job URL: https://example.com/job/3", prompt)
        self.assertIn("Run 3 local log path: /tmp/run3.log", prompt)
        self.assertNotIn("Run 1 job URL:", prompt)

    def test_fallback_skips_empty_job_urls_before_zipping(self) -> None:
        job = {
            "workflow_name": "wf",
            "job_name": "job",
            "job_urls": ["", "https://example.com/job/2", "https://example.com/job/3"],
            "run_urls": [],
        }
        captured_prompt: dict[str, str] = {}

        def _fake_agent(prompt: str) -> str:
            captured_prompt["text"] = prompt
            payload = {
                "deterministic": True,
                "confidence": "high",
                "issue_title": "x",
                "issue_body": "y",
            }
            return f"{draft_issues.MARKER}\n{json.dumps(payload)}"

        with patch(
            "tools.ci.create_issues.draft_issues._run_llm_agent",
            side_effect=_fake_agent,
        ):
            result = draft_issues.draft_issue_body(job, ["/tmp/run2.log", "/tmp/run3.log"], consecutive=2)

        self.assertIsNotNone(result)
        prompt = captured_prompt["text"]
        self.assertIn("Run 1 job URL: https://example.com/job/2", prompt)
        self.assertIn("Run 1 local log path: /tmp/run2.log", prompt)
        self.assertIn("Run 2 job URL: https://example.com/job/3", prompt)
        self.assertIn("Run 2 local log path: /tmp/run3.log", prompt)
        self.assertNotIn("Run 1 job URL: \nRun 1 local log path: /tmp/run2.log", prompt)


class RunCopilotAgentModelTests(unittest.TestCase):
    def _run_and_capture_cmd(self, env: dict[str, str]) -> list[str]:
        captured: dict[str, list[str]] = {}

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

        with patch.dict("os.environ", env, clear=True), patch(
            "tools.ci.create_issues.draft_issues.subprocess.run",
            side_effect=_fake_run,
        ):
            draft_issues._run_copilot_agent("prompt")
        return captured["cmd"]

    def test_passes_default_model_to_copilot(self) -> None:
        cmd = self._run_and_capture_cmd({"PATH": "/usr/bin"})
        self.assertEqual(cmd[cmd.index("--model") + 1], draft_issues._DEFAULT_MODEL)
        self.assertEqual(draft_issues._DEFAULT_MODEL, "claude-sonnet-5.5")

    def test_copilot_model_env_overrides_default(self) -> None:
        cmd = self._run_and_capture_cmd({"PATH": "/usr/bin", "COPILOT_MODEL": "claude-opus-5.5"})
        self.assertEqual(cmd[cmd.index("--model") + 1], "claude-opus-5.5")


if __name__ == "__main__":
    unittest.main()
