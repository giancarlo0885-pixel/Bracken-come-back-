"""Regression: malformed walk-forward calibration must never authorize paper size boosts.

Research gate only; no order, accounting, or broker code is exercised.
"""
from __future__ import annotations

import sys
import types

import pytest

import paper_model_validation_gate as gate


_VALID = {
    "sample_count": 25,
    "brier_skill_score": 0.15,
    "expected_calibration_error": 0.08,
}


def _evaluate(monkeypatch, bear_metrics):
    database = types.ModuleType("database")
    database.rows = lambda *_args, **_kwargs: [{
        "status": "PASS",
        "regime_metrics": {"bull": dict(_VALID), "bear": bear_metrics},
    }]
    monkeypatch.setitem(sys.modules, "database", database)
    monkeypatch.setenv("PAPER_MODEL_MIN_VALIDATED_REGIMES", "2")
    monkeypatch.setenv("PAPER_MODEL_MIN_REGIME_SAMPLES", "20")
    monkeypatch.setenv("PAPER_MODEL_MIN_REGIME_BRIER_SKILL", "0.0")
    monkeypatch.setenv("PAPER_MODEL_MAX_REGIME_ECE", "0.18")
    return gate.regime_validation_ok({"model": "causal-model", "model_version": "v1"})


def test_valid_calibration_across_two_regimes_remains_eligible(monkeypatch):
    assert _evaluate(monkeypatch, dict(_VALID)) == (True, "regime_validation_pass:2")


@pytest.mark.parametrize(
    "field,value",
    [
        ("brier_skill_score", float("inf")),
        ("brier_skill_score", True),
        ("brier_skill_score", 1.2),
        ("expected_calibration_error", -0.05),
        ("expected_calibration_error", float("-inf")),
        ("expected_calibration_error", False),
        ("sample_count", "twenty-five"),
        ("sample_count", float("nan")),
        ("sample_count", 25.5),
    ],
)
def test_malformed_calibration_cannot_qualify_second_regime(monkeypatch, field, value):
    bear = dict(_VALID)
    bear[field] = value
    eligible, reason = _evaluate(monkeypatch, bear)
    assert eligible is False
    assert reason.startswith("regime_validation_insufficient:1/2")


def test_finite_numeric_strings_remain_compatible(monkeypatch):
    bear = {
        "sample_count": "25",
        "brier_skill_score": "0.15",
        "expected_calibration_error": "0.08",
    }
    assert _evaluate(monkeypatch, bear) == (True, "regime_validation_pass:2")
