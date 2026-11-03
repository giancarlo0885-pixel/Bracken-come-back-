"""Regression-first: malformed model evidence must never qualify paper capital sizing.

The tests use synthetic evidence and make no claim about production incidence.
"""
from __future__ import annotations

import pytest

from paper_model_governance import (
    PAPER_EXPLORATORY,
    PAPER_QUALIFIED,
    RESEARCH,
    classify_paper_model_metrics,
)


def _valid_metrics():
    return {
        "sample_count": 1000,
        "directional_accuracy": 0.55,
        "expected_calibration_error": 0.02,
        "brier_skill_score": 0.10,
        "closed_paper_trades": 40,
        "paper_expectancy": 0.02,
        "paper_profit_factor": 1.2,
    }


@pytest.mark.parametrize(
    ("field", "malformed", "expected_tier"),
    [
        ("directional_accuracy", True, RESEARCH),
        ("directional_accuracy", 1.01, RESEARCH),
        ("expected_calibration_error", False, RESEARCH),
        ("expected_calibration_error", -0.01, RESEARCH),
        ("brier_skill_score", True, RESEARCH),
        ("brier_skill_score", 1.01, RESEARCH),
        ("paper_expectancy", True, PAPER_EXPLORATORY),
        ("paper_profit_factor", True, PAPER_EXPLORATORY),
    ],
)
def test_malformed_calibration_or_economics_cannot_promote(
    monkeypatch, field, malformed, expected_tier
):
    monkeypatch.setenv("PAPER_MODEL_MIN_VALIDATION_SAMPLES", "1000")
    monkeypatch.setenv("PAPER_QUALIFIED_MIN_CLOSED_TRADES", "30")
    monkeypatch.setenv("PAPER_QUALIFIED_MIN_EXPECTANCY", "0.0")
    monkeypatch.setenv("PAPER_QUALIFIED_MIN_PROFIT_FACTOR", "1.0")
    evidence = _valid_metrics()
    evidence[field] = malformed

    result = classify_paper_model_metrics(
        "model", "v1", evidence,
        temporal_leakage_ok=True,
        recent_walk_forward_runs=3,
        distinct_symbols=3,
    )

    assert result.tier == expected_tier
    assert result.paper_qualified is False
    assert result.capital_qualified is False


def test_valid_numeric_string_evidence_still_qualifies(monkeypatch):
    monkeypatch.setenv("PAPER_MODEL_MIN_VALIDATION_SAMPLES", "1000")
    evidence = _valid_metrics()
    for key in (
        "directional_accuracy", "expected_calibration_error",
        "brier_skill_score", "paper_expectancy", "paper_profit_factor",
    ):
        evidence[key] = str(evidence[key])

    result = classify_paper_model_metrics(
        "model", "v1", evidence,
        temporal_leakage_ok=True,
        recent_walk_forward_runs=3,
        distinct_symbols=3,
    )
    assert result.tier == PAPER_QUALIFIED
    assert result.capital_qualified is False
