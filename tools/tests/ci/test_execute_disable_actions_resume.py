from __future__ import annotations

import pytest

from tools.ci.execute_disable_actions import (
    evaluate_resume_failure_confirmation,
    has_issue_reference_in_working_diff,
)


def test_evaluate_resume_failure_confirmation_requires_two_matching_failures() -> None:
    item: dict[str, object] = {}
    failure_signal = {
        "workflow": "all-static-checks.yaml",
        "target": "tests/foo.py::test_bar",
        "error": "assertion failed",
        "details": {"new_failure_target": "tests/foo.py::test_bar"},
    }
    first = evaluate_resume_failure_confirmation(item=item, failure_signal=failure_signal)
    assert first["decision"] == "wait"
    second = evaluate_resume_failure_confirmation(item=item, failure_signal=failure_signal)
    assert second["decision"] == "proceed"


def test_evaluate_resume_failure_confirmation_escalates_on_signature_mismatch() -> None:
    item: dict[str, object] = {}
    sig1 = {"workflow": "wf", "target": "a", "error": "e1", "details": {}}
    sig2 = {"workflow": "wf", "target": "b", "error": "e2", "details": {}}
    first = evaluate_resume_failure_confirmation(item=item, failure_signal=sig1)
    assert first["decision"] == "wait"
    second = evaluate_resume_failure_confirmation(item=item, failure_signal=sig2)
    assert second["decision"] == "escalate"


def test_has_issue_reference_in_working_diff_detects_added_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.ci.execute_disable_actions as mod

    class _Proc:
        stdout = "diff --git a/file b/file\n" "@@ -1 +1 @@\n" "+# TODO(#123): temporarily disable flaky test\n"

    monkeypatch.setattr(mod, "run", lambda *_args, **_kwargs: _Proc())
    assert has_issue_reference_in_working_diff(issue_number=123, issue_url="https://github.com/x/y/issues/123")
