"""Fail-first: provenance and walk-forward coverage require unambiguous evidence.

Synthetic evidence only. No production incidence or profit claim is implied.
"""
from decimal import Decimal

import pytest

from paper_model_governance import PAPER_QUALIFIED, classify_paper_model_metrics


def _assess(*, leakage=True, runs=3, symbols=3):
    return classify_paper_model_metrics(
        "synthetic", "v1",
        {
            "sample_count": 1000,
            "directional_accuracy": 0.55,
            "expected_calibration_error": 0.02,
            "brier_skill_score": 0.10,
            "closed_paper_trades": 30,
            "paper_expectancy": 0.02,
            "paper_profit_factor": 1.2,
        },
        temporal_leakage_ok=leakage,
        recent_walk_forward_runs=runs,
        distinct_symbols=symbols,
    )


@pytest.mark.parametrize("invalid", ["false", "true", 1, 0, None, [], {"clean": True}])
def test_ambiguous_temporal_leakage_evidence_never_qualifies(invalid):
    result = _assess(leakage=invalid)
    assert result.paper_qualified is False
    assert result.capital_qualified is False
    assert result.temporal_leakage_ok is False


@pytest.mark.parametrize("field,invalid", [
    ("runs", True), ("runs", 3.5), ("runs", Decimal("3.5")),
    ("runs", float("nan")), ("runs", "invalid"), ("runs", None),
    ("symbols", True), ("symbols", 2.5), ("symbols", Decimal("2.5")),
    ("symbols", float("nan")), ("symbols", "invalid"), ("symbols", None),
])
def test_invalid_external_evidence_counts_fail_closed_without_exception(field, invalid):
    result = _assess(**{field: invalid})
    assert result.paper_qualified is False
    assert result.capital_qualified is False
    assert getattr(result, "recent_walk_forward_runs" if field == "runs" else "distinct_symbols") == 0


@pytest.mark.parametrize("runs,symbols", [
    (3, 3), ("3", "3"), (3.0, 3.0), (Decimal("3"), Decimal("3")),
])
def test_integral_walk_forward_counts_preserve_legitimate_paper_qualification(runs, symbols):
    result = _assess(runs=runs, symbols=symbols)
    assert result.tier == PAPER_QUALIFIED
    assert result.paper_qualified is True
    assert result.capital_qualified is False
    assert result.temporal_leakage_ok is True
    assert result.recent_walk_forward_runs == 3
    assert result.distinct_symbols == 3


def test_explicit_leakage_false_remains_a_veto():
    result = _assess(leakage=False)
    assert result.paper_qualified is False
    assert result.temporal_leakage_ok is False
