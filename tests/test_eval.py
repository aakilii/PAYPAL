"""Tests for M9: Verify all 24 scripted evaluation cases in eval_cases.json pass."""

from __future__ import annotations

from scripts.run_eval import load_eval_cases, run_suite


def test_eval_cases_count_and_mock_suite() -> None:
    cases = load_eval_cases()
    assert len(cases) == 24

    results = run_suite("mock")
    assert len(results) == 24
    failures = [r for r in results if not r["passed"]]
    assert failures == []
