"""Fail-first capital-model governance: impossible or future evidence cannot approve.

Synthetic fixtures only. No assertion of production occurrence or profitability.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from capital_model_governance import assess_model_evidence


_NOW = datetime(2026, 11, 3, 15, 50, tzinfo=timezone.utc)
_VALID = {
    "brier_skill_score": 0.10,
    "expected_calibration_error": 0.05,
    "directional_accuracy": 0.60,
    "beats_all_baselines": True,
}
_LEAKAGE = {"strict_ordering": True, "future_mutation_probe": {"ok": True}}


def _assess(monkeypatch, *, second_metrics=None, second_time=None):
    monkeypatch.setenv("CAPITAL_MIN_MODEL_PASS_RUNS", "2")
    monkeypatch.setenv("CAPITAL_MIN_MODEL_SYMBOLS", "2")
    monkeypatch.setenv("CAPITAL_MIN_BRIER_SKILL", "0.02")
    monkeypatch.setenv("CAPITAL_MAX_ECE", "0.12")
    monkeypatch.setenv("CAPITAL_MIN_DIRECTIONAL_ACCURACY", "0.52")
    evidence = [
        {
            "model": "synthetic", "model_version": "v1", "symbol": symbol,
            "run_id": symbol, "status": "PASS",
            "metrics": dict(_VALID) if index == 0 else (
                dict(_VALID) if second_metrics is None else dict(second_metrics)
            ),
            "leakage_checks": dict(_LEAKAGE),
            "created_at": _NOW.isoformat() if index == 0 else (
                _NOW.isoformat() if second_time is None else second_time
            ),
        }
        for index, symbol in enumerate(("AAA", "BBB"))
    ]
    return assess_model_evidence(
        "synthetic", "v1", evidence, current_status="shadow", now=_NOW
    )


@pytest.mark.parametrize("field,value", [
    ("brier_skill_score", float("inf")),
    ("brier_skill_score", True),
    ("brier_skill_score", 1.2),
    ("expected_calibration_error", -0.01),
    ("expected_calibration_error", False),
    ("directional_accuracy", 1.1),
    ("directional_accuracy", True),
    ("directional_accuracy", "not-a-number"),
])
def test_impossible_or_ambiguous_metric_never_approves(monkeypatch, field, value):
    metrics = dict(_VALID)
    metrics[field] = value
    result = _assess(monkeypatch, second_metrics=metrics)
    assert result.eligible_for_approval is False
    assert result.passing_run_count == 1
    assert result.recommended_status == "shadow"


def test_future_dated_run_cannot_count_as_current_forward_evidence(monkeypatch):
    result = _assess(
        monkeypatch, second_time=(_NOW + timedelta(days=1)).isoformat()
    )
    assert result.eligible_for_approval is False
    assert result.passing_run_count == 1
    assert result.recent_run_count == 1
    assert result.recommended_status == "shadow"


def test_valid_point_in_time_finite_evidence_retains_existing_approval(monkeypatch):
    result = _assess(monkeypatch)
    assert result.eligible_for_approval is True
    assert result.passing_run_count == 2
    assert result.recommended_status == "approved"
