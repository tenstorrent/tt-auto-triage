from __future__ import annotations

from tools.ci.testing_thread_persona_session import build_summary


def test_build_summary_contains_scenario_rows() -> None:
    md = build_summary(
        {
            "anchor_ts": "123.45",
            "scenarios": [
                {
                    "name": "active_plan",
                    "progress_state": "fix_in_progress",
                    "defer_disable": True,
                    "fix_request_requested": False,
                }
            ],
        }
    )
    assert "Thread Persona Simulation Session" in md
    assert "active_plan" in md
