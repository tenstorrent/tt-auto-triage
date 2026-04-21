"""Tests for the agent-facing `check_active` employment-check CLI.

The point of the CLI is to be the single source of truth for the agent,
so tests must pin:

1. The reasons it emits match `is_active_employee` in `__main__.py`.
2. It honors EX_EMPLOYEES from the environment for emergencies.
3. It never errors out on missing/empty Slack dumps — it just can't block.
"""
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tools.ci.assign_owners import check_active as mod


def _slack(sid: str, deleted: bool = False) -> dict:
    return {"id": sid, "deleted": deleted, "real_name": "X", "display_name": "x"}


def _write_dump(tmp: Path, users: list[dict]) -> Path:
    p = tmp / "slack_users.json"
    p.write_text(json.dumps(users), encoding="utf-8")
    return p


def _run(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = mod.main(argv)
    return code, buf.getvalue().strip()


class CheckActiveCliTests(unittest.TestCase):
    def test_active_when_user_present_and_not_deleted(self) -> None:
        with TemporaryDirectory() as d:
            dump = _write_dump(Path(d), [_slack("U1")])
            code, out = _run(["--slack-dump", str(dump), "--slack-id", "U1"])
        self.assertEqual(code, 0)
        self.assertEqual(out, "ACTIVE")

    def test_slack_deleted_alone_is_sufficient_to_block(self) -> None:
        with TemporaryDirectory() as d:
            dump = _write_dump(Path(d), [_slack("U1", deleted=True)])
            code, out = _run(["--slack-dump", str(dump), "--slack-id", "U1"])
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("INACTIVE:"), out)
        self.assertIn("deactivated", out)

    def test_missing_from_non_empty_dump_blocks(self) -> None:
        with TemporaryDirectory() as d:
            dump = _write_dump(Path(d), [_slack("U1")])
            code, out = _run(["--slack-dump", str(dump), "--slack-id", "UGONE"])
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("INACTIVE:"), out)
        self.assertIn("not present", out)

    def test_empty_dump_cannot_block(self) -> None:
        with TemporaryDirectory() as d:
            dump = _write_dump(Path(d), [])
            code, out = _run(["--slack-dump", str(dump), "--slack-id", "UGONE"])
        self.assertEqual(code, 0)
        self.assertEqual(out, "ACTIVE")

    def test_missing_dump_file_cannot_block(self) -> None:
        with TemporaryDirectory() as d:
            code, out = _run(["--slack-dump", str(Path(d) / "missing.json"),
                              "--slack-id", "U1"])
        self.assertEqual(code, 0)
        self.assertEqual(out, "ACTIVE")

    def test_ex_employees_env_override_by_slack_id(self) -> None:
        with TemporaryDirectory() as d:
            dump = _write_dump(Path(d), [_slack("U1")])
            with patch.dict("os.environ", {"EX_EMPLOYEES": "U1"}, clear=False):
                code, out = _run(["--slack-dump", str(dump), "--slack-id", "U1"])
        self.assertEqual(code, 0)
        self.assertIn("ex-employees override (slack_id)", out)

    def test_ex_employees_env_override_by_github_login(self) -> None:
        with TemporaryDirectory() as d:
            dump = _write_dump(Path(d), [_slack("U1")])
            with patch.dict("os.environ", {"EX_EMPLOYEES": "alice-gh,U999"}, clear=False):
                code, out = _run(["--slack-dump", str(dump),
                                  "--slack-id", "U1", "--login", "alice-gh"])
        self.assertEqual(code, 0)
        self.assertIn("ex-employees override (github login)", out)

    def test_requires_at_least_one_identifier(self) -> None:
        with TemporaryDirectory() as d:
            dump = _write_dump(Path(d), [_slack("U1")])
            code, _ = _run(["--slack-dump", str(dump)])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
