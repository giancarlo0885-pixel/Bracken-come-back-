from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Iterable

from config import (
    FORECAST_MAX_CALIBRATION_ERROR,
    FORECAST_MIN_DIRECTIONAL_ACCURACY,
    FORECAST_MIN_VALIDATION_SAMPLES,
)
from database import rows
from forecast_calibration import CalibrationMetrics, evaluate_probability_calibration
from model_registry import ModelStatus, model_status
from provider_router import normalize_symbol


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


@dataclass
class ForecastValidationSummary:
    symbol: str
    asset_class: str
    source_interval: str
    model: str
    model_version: str
    directional_accuracy: float
    mean_absolute_percentage_error: float
    calibration_error: float
    average_predicted_move: float
    average_realized_move: float
    sample_count: int
    calibration_sample_count: int = 0
    directional_accuracy_ci_low: float | None = None
    directional_accuracy_ci_high: float | None = None
    brier_score: float | None = None
    climatology_brier_score: float | None = None
    brier_skill_score: float | None = None
    log_loss: float | None = None
    expected_calibration_error: float | None = None
    maximum_calibration_error: float | None = None
    calibration_intercept: float | None = None
    calibration_slope: float | None = None
    reliability_bins: list[dict[str, Any]] = field(default_factory=list)
    forecast_interval_coverage: float | None = None
    effective_sample_size: float = 0.0

    @property
    def reliability_buckets(self) -> list[dict[str, Any]]:
        return self.reliability_bins

    @property
    def calibration_status(self) -> str:
        """Classify the probability output without overstating model certainty."""
        if self.calibration_sample_count < FORECAST_MIN_VALIDATION_SAMPLES:
            return "INSUFFICIENT_EVIDENCE"
        if self.expected_calibration_error is None or self.brier_skill_score is None:
            return "INSUFFICIENT_EVIDENCE"
        if self.expected_calibration_error > FORECAST_MAX_CALIBRATION_ERROR:
            return "MIS_CALIBRATED"
        if self.brier_skill_score <= 0.0:
            return "NO_SKILL_VS_BASE_RATE"
        if self.directional_accuracy < FORECAST_MIN_DIRECTIONAL_ACCURACY:
            return "DIRECTIONALLY_WEAK"
        return "EMPIRICALLY_CALIBRATED"

    @property
    def confidence_kind(self) -> str:
        return "CALIBRATED_PROBABILITY" if self.calibration_status == "EMPIRICALLY_CALIBRATED" else "HEURISTIC_SCORE"

    @property
    def execution_approved(self) -> bool:
        if self.sample_count < FORECAST_MIN_VALIDATION_SAMPLES:
            return False
        if self.calibration_sample_count < FORECAST_MIN_VALIDATION_SAMPLES:
            return False
        if self.directional_accuracy < FORECAST_MIN_DIRECTIONAL_ACCURACY:
            return False
        if self.expected_calibration_error is None:
            return False
        if self.expected_calibration_error > FORECAST_MAX_CALIBRATION_ERROR:
            return False
        # A model must beat the empirical base-rate forecast. Merely looking
        # calibrated in aggregate is not enough to call its probabilities useful.
        if self.brier_skill_score is None or self.brier_skill_score <= 0.0:
            return False
        return True


def _empirical_calibration(items: list[dict[str, Any]]) -> CalibrationMetrics:
    normalized: list[dict[str, Any]] = []
    for item in items:
        row = dict(item)
        probability = row.get("probability_up")
        try:
            value = float(probability)
        except (TypeError, ValueError):
            value = float("nan")
        if math.isfinite(value) and value > 1.0:
            value /= 100.0
        row["probability_up"] = value if math.isfinite(value) else None
        normalized.append(row)
    return evaluate_probability_calibration(normalized, bins=10)


def summarize_validation(records: Iterable[dict[str, Any]]) -> ForecastValidationSummary:
    items = list(records)
    if not items:
        return ForecastValidationSummary("", "", "", "", "", 0.0, 0.0, 1.0, 0.0, 0.0, 0)

    count = len(items)
    correct = sum(1 for item in items if bool(item.get("direction_correct")))
    mape = sum(_finite(item.get("mape")) for item in items) / count
    predicted = [_finite(item.get("predicted_move_pct")) for item in items]
    realized = [_finite(item.get("realized_move_pct")) for item in items]
    empirical = _empirical_calibration(items)
    coverage_items = [
        item
        for item in items
        if item.get("low_move_pct") is not None and item.get("high_move_pct") is not None
    ]
    coverage = None
    if coverage_items:
        coverage = sum(
            1
            for item in coverage_items
            if _finite(item.get("low_move_pct")) <= _finite(item.get("realized_move_pct")) <= _finite(item.get("high_move_pct"))
        ) / len(coverage_items)
    first = items[0]

    # calibration_error remains the compatibility field consumed elsewhere,
    # but it now means ECE across reliability buckets rather than the old
    # difference between two global averages.
    ece = empirical.expected_calibration_error
    return ForecastValidationSummary(
        symbol=normalize_symbol(first.get("symbol")),
        asset_class=str(first.get("asset_class") or ""),
        source_interval=str(first.get("source_interval") or ""),
        model=str(first.get("model") or ""),
        model_version=str(first.get("model_version") or ""),
        directional_accuracy=empirical.directional_accuracy if empirical.directional_accuracy is not None else correct / count,
        mean_absolute_percentage_error=mape,
        calibration_error=ece if ece is not None else 1.0,
        average_predicted_move=sum(predicted) / count,
        average_realized_move=sum(realized) / count,
        sample_count=count,
        calibration_sample_count=empirical.sample_count,
        directional_accuracy_ci_low=empirical.directional_accuracy_ci_low,
        directional_accuracy_ci_high=empirical.directional_accuracy_ci_high,
        brier_score=empirical.brier_score,
        climatology_brier_score=empirical.climatology_brier_score,
        brier_skill_score=empirical.brier_skill_score,
        log_loss=empirical.log_loss,
        expected_calibration_error=empirical.expected_calibration_error,
        maximum_calibration_error=empirical.maximum_calibration_error,
        calibration_intercept=empirical.calibration_intercept,
        calibration_slope=empirical.calibration_slope,
        reliability_bins=[item.to_dict() for item in empirical.bins],
        forecast_interval_coverage=coverage,
        effective_sample_size=float(empirical.sample_count),
    )


def validation_summary(symbol: str, asset_class: str, source_interval: str, model: str, model_version: str = "") -> ForecastValidationSummary:
    records = rows(
        """
        SELECT symbol, asset_class, source_interval, model, model_version,
               probability_up, predicted_move_pct, realized_move_pct,
               direction_correct, mape
        FROM forecast_validation
        WHERE symbol=%s AND asset_class=%s AND source_interval=%s AND model=%s
          AND COALESCE(model_version, '') = COALESCE(%s, '')
        ORDER BY id DESC
        LIMIT 500
        """,
        (normalize_symbol(symbol), asset_class, source_interval, model, model_version),
    )
    return summarize_validation(records)


def model_execution_approved(symbol: str, asset_class: str, source_interval: str, model: str, model_version: str = "") -> tuple[bool, str]:
    if FORECAST_MIN_VALIDATION_SAMPLES <= 0:
        return True, "forecast validation gate disabled"
    status = model_status(model, model_version)
    if status in {ModelStatus.EXPERIMENTAL, ModelStatus.SHADOW, ModelStatus.DISABLED}:
        return False, f"forecast model status is {status.value}"

    summary = validation_summary(symbol, asset_class, source_interval, model, model_version)
    if summary.sample_count < FORECAST_MIN_VALIDATION_SAMPLES:
        return False, f"forecast model has {summary.sample_count} validation samples; needs {FORECAST_MIN_VALIDATION_SAMPLES}"
    if summary.calibration_sample_count < FORECAST_MIN_VALIDATION_SAMPLES:
        return False, (
            f"forecast model has only {summary.calibration_sample_count} probability/outcome pairs; "
            f"needs {FORECAST_MIN_VALIDATION_SAMPLES}"
        )
    if summary.directional_accuracy < FORECAST_MIN_DIRECTIONAL_ACCURACY:
        return False, f"forecast directional accuracy {summary.directional_accuracy:.2f} is below {FORECAST_MIN_DIRECTIONAL_ACCURACY:.2f}"
    if summary.expected_calibration_error is None:
        return False, "forecast probability calibration is unavailable"
    if summary.expected_calibration_error > FORECAST_MAX_CALIBRATION_ERROR:
        return False, (
            f"forecast expected calibration error {summary.expected_calibration_error:.3f} "
            f"exceeds {FORECAST_MAX_CALIBRATION_ERROR:.3f}"
        )
    if summary.brier_skill_score is None:
        return False, "forecast Brier skill is unavailable"
    if summary.brier_skill_score <= 0.0:
        return False, f"forecast Brier skill {summary.brier_skill_score:.3f} does not beat the empirical base rate"
    return True, (
        "forecast validation approved; "
        f"calibration={summary.calibration_status}; "
        f"brier={summary.brier_score:.4f}; brier_skill={summary.brier_skill_score:.3f}; "
        f"ece={summary.expected_calibration_error:.3f}"
    )
