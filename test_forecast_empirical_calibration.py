from __future__ import annotations

import math

import forecast_quality
from forecast_calibration import evaluate_probability_calibration, wilson_interval


def _record(probability: float, outcome_up: bool, *, symbol: str = "BTC-USD") -> dict:
    realized = 1.0 if outcome_up else -1.0
    predicted = 1.0 if probability >= 0.5 else -1.0
    return {
        "symbol": symbol,
        "asset_class": "crypto",
        "source_interval": "1h",
        "model": "empirical-test",
        "model_version": "v1",
        "probability_up": probability,
        "predicted_move_pct": predicted,
        "realized_move_pct": realized,
        "direction_correct": (predicted > 0) == outcome_up,
        "mape": 0.10,
    }


def _well_calibrated_records() -> list[dict]:
    records: list[dict] = []
    for probability, positives in ((0.1, 1), (0.3, 3), (0.7, 7), (0.9, 9)):
        records.extend(_record(probability, index < positives) for index in range(10))
    return records


def test_probability_calibration_scores_realized_outcomes():
    metrics = evaluate_probability_calibration(_well_calibrated_records(), bins=10)

    assert metrics.sample_count == 40
    assert metrics.base_rate == 0.5
    assert metrics.directional_accuracy == 0.8
    assert metrics.brier_score is not None and metrics.brier_score < 0.20
    assert metrics.climatology_brier_score == 0.25
    assert metrics.brier_skill_score is not None and metrics.brier_skill_score > 0.0
    assert metrics.expected_calibration_error is not None
    assert metrics.expected_calibration_error < 1e-12
    assert metrics.maximum_calibration_error is not None
    assert metrics.maximum_calibration_error < 1e-12
    assert len(metrics.bins) == 4
    assert metrics.directional_accuracy_ci_low is not None
    assert metrics.directional_accuracy_ci_high is not None


def test_calibration_excludes_invalid_probability_or_missing_outcome_rows():
    rows = _well_calibrated_records()[:2]
    rows.extend(
        [
            {"probability_up": 1.5, "realized_move_pct": 1.0},
            {"probability_up": 0.5, "realized_move_pct": None},
            {"probability_up": float("nan"), "realized_move_pct": -1.0},
        ]
    )
    metrics = evaluate_probability_calibration(rows)
    assert metrics.sample_count == 2


def test_forecast_summary_labels_only_empirically_supported_probability(monkeypatch):
    monkeypatch.setattr(forecast_quality, "FORECAST_MIN_VALIDATION_SAMPLES", 30)
    monkeypatch.setattr(forecast_quality, "FORECAST_MIN_DIRECTIONAL_ACCURACY", 0.52)
    monkeypatch.setattr(forecast_quality, "FORECAST_MAX_CALIBRATION_ERROR", 0.18)

    summary = forecast_quality.summarize_validation(_well_calibrated_records())

    assert summary.sample_count == 40
    assert summary.calibration_sample_count == 40
    assert summary.calibration_status == "EMPIRICALLY_CALIBRATED"
    assert summary.confidence_kind == "CALIBRATED_PROBABILITY"
    assert summary.execution_approved is True
    assert summary.calibration_error == summary.expected_calibration_error
    assert summary.brier_skill_score is not None and summary.brier_skill_score > 0.0


def test_overconfident_probabilities_fail_empirical_calibration(monkeypatch):
    monkeypatch.setattr(forecast_quality, "FORECAST_MIN_VALIDATION_SAMPLES", 30)
    monkeypatch.setattr(forecast_quality, "FORECAST_MIN_DIRECTIONAL_ACCURACY", 0.40)
    monkeypatch.setattr(forecast_quality, "FORECAST_MAX_CALIBRATION_ERROR", 0.18)

    records = [_record(0.9, index % 2 == 0) for index in range(40)]
    summary = forecast_quality.summarize_validation(records)

    assert summary.expected_calibration_error is not None
    assert summary.expected_calibration_error > 0.18
    assert summary.calibration_status == "MIS_CALIBRATED"
    assert summary.confidence_kind == "HEURISTIC_SCORE"
    assert summary.execution_approved is False


def test_base_rate_forecast_is_not_mistaken_for_useful_calibration(monkeypatch):
    monkeypatch.setattr(forecast_quality, "FORECAST_MIN_VALIDATION_SAMPLES", 30)
    monkeypatch.setattr(forecast_quality, "FORECAST_MIN_DIRECTIONAL_ACCURACY", 0.0)
    monkeypatch.setattr(forecast_quality, "FORECAST_MAX_CALIBRATION_ERROR", 0.18)

    records = [_record(0.5, index % 2 == 0) for index in range(40)]
    summary = forecast_quality.summarize_validation(records)

    assert summary.expected_calibration_error == 0.0
    assert summary.brier_score == summary.climatology_brier_score
    assert math.isclose(summary.brier_skill_score or 0.0, 0.0, abs_tol=1e-12)
    assert summary.calibration_status == "NO_SKILL_VS_BASE_RATE"
    assert summary.execution_approved is False


def test_insufficient_probability_pairs_cannot_receive_calibrated_label(monkeypatch):
    monkeypatch.setattr(forecast_quality, "FORECAST_MIN_VALIDATION_SAMPLES", 30)
    records = _well_calibrated_records()[:20]
    summary = forecast_quality.summarize_validation(records)

    assert summary.calibration_status == "INSUFFICIENT_EVIDENCE"
    assert summary.confidence_kind == "HEURISTIC_SCORE"
    assert summary.execution_approved is False


def test_wilson_interval_contains_observed_accuracy():
    low, high = wilson_interval(80, 100)
    assert low is not None and high is not None
    assert low < 0.8 < high
