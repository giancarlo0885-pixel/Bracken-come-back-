"""Regression-first: only complete, finite count evidence may qualify paper models.

Synthetic fixtures only; no production occurrence or economic benefit is implied.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from paper_model_governance import PAPER_QUALIFIED, classify_paper_model_metrics


def _valid():
    return {
        "sample_count": 1000,
        "directional_accuracy": 0.55,
        "expected_calibration_error": 0.02,
        "brier_skill_score": 0.10,
        "closed_paper_trades": 30,
        "paper_expectancy": 0.02,
        "paper_profit_factor": 1.2,
    }


def _assess(metrics):
    return classify_paper_model_metrics(
        "synthetic", "v1", metrics,
        temporal_leakage_ok=True,
        recent_walk_forward_runs=3,
        distinct_symbols=3,
    )


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("sample_count", 1000.5),
        ("sample_count", "not-a-count"),
        ("sample_count", float("nan")),
        ("sample_count", float("inf")),
        ("sample_count", Decimal("1000.5")),
        ("closed_paper_trades", 30.5),
        ("closed_paper_trades", "not-a-count"),
        ("closed_paper_trades", float("nan")),
        ("closed_paper_trades", float("inf")),
        ("closed_paper_trades", Decimal("30.5")),
    ],
)
def test_invalid_counts_fail_closed_without_exception(field, invalid):
    metrics = _valid()
    metrics[field] = invalid
    result = _assess(metrics)
    assert result.paper_qualified is False
    assert result.capital_qualified is False
    assert getattr(result, field) == 0


@pytest.mark.parametrize(
    ("sample_count", "closed_paper_trades"),
    [
        (1000, 30),
        ("1000", "30"),
        (1000.0, 30.0),
        (Decimal("1000"), Decimal("30")),
    ],
)
def test_legitimate_integral_counts_preserve_qualification(sample_count, closed_paper_trades):
    metrics = _valid()
    metrics["sample_count"] = sample_count
    metrics["closed_paper_trades"] = closed_paper_trades
    result = _assess(metrics)
    assert result.tier == PAPER_QUALIFIED
    assert result.paper_qualified is True
    assert result.capital_qualified is False
