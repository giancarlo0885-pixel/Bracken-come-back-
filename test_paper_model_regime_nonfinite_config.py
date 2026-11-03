"""Fail-first: nonfinite walk-forward gate configuration must not authorize sizing.

Research-only; production accounting, thresholds and execution controls unchanged.
"""
from __future__ import annotations

import sys
import types

import pytest

import paper_model_validation_gate as gate


def _check(monkeypatch, *, brier: float, ece: float):
    db = types.ModuleType("database")
    metrics = {
        "sample_count": 25,
        "brier_skill_score": brier,
        "expected_calibration_error": ece,
    }
    db.rows = lambda *_args, **_kwargs: [{
        "status": "PASS",
        "regime_metrics": {"up": dict(metrics), "down": dict(metrics)},
    }]
    monkeypatch.setitem(sys.modules, "database", db)
    monkeypatch.setenv("PAPER_MODEL_MIN_VALIDATED_REGIMES", "2")
    monkeypatch.setenv("PAPER_MODEL_MIN_REGIME_SAMPLES", "20")
    return gate.regime_validation_ok({"model": "test-model", "model_version": "v1"})


@pytest.mark.parametrize("bad_ece_limit", ["nan", "inf", "-inf"])
def test_nonfinite_ece_limit_cannot_authorize_paper_size(monkeypatch, bad_ece_limit):
    monkeypatch.setenv("PAPER_MODEL_MIN_REGIME_BRIER_SKILL", "0.0")
    monkeypatch.setenv("PAPER_MODEL_MAX_REGIME_ECE", bad_ece_limit)
    assert _check(monkeypatch, brier=0.15, ece=0.8) == (
        False, "regime_config_invalid"
    )


@pytest.mark.parametrize("bad_skill_floor", ["nan", "inf", "-inf"])
def test_nonfinite_brier_floor_cannot_authorize_paper_size(monkeypatch, bad_skill_floor):
    monkeypatch.setenv("PAPER_MODEL_MIN_REGIME_BRIER_SKILL", bad_skill_floor)
    monkeypatch.setenv("PAPER_MODEL_MAX_REGIME_ECE", "0.18")
    assert _check(monkeypatch, brier=-0.5, ece=0.08) == (
        False, "regime_config_invalid"
    )


def test_valid_finite_thresholds_retain_existing_behavior(monkeypatch):
    monkeypatch.setenv("PAPER_MODEL_MIN_REGIME_BRIER_SKILL", "-0.2")
    monkeypatch.setenv("PAPER_MODEL_MAX_REGIME_ECE", "0.18")
    assert _check(monkeypatch, brier=-0.1, ece=0.08) == (
        True, "regime_validation_pass:2"
    )
