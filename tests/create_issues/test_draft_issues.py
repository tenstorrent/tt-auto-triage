import json
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


if __name__ == "__main__":
    unittest.main()
