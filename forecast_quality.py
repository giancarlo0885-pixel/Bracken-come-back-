from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from config import (
    FORECAST_MAX_CALIBRATION_ERROR,
    FORECAST_MIN_DIRECTIONAL_ACCURACY,
    FORECAST_MIN_VALIDATION_SAMPLES,
)
from database import rows
from provider_router import normalize_symbol


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

    @property
    def execution_approved(self) -> bool:
        if self.sample_count < FORECAST_MIN_VALIDATION_SAMPLES:
            return False
        if self.directional_accuracy < FORECAST_MIN_DIRECTIONAL_ACCURACY:
            return False
        return self.calibration_error <= FORECAST_MAX_CALIBRATION_ERROR


def summarize_validation(records: Iterable[dict[str, Any]]) -> ForecastValidationSummary:
    items = list(records)
    if not items:
        return ForecastValidationSummary("", "", "", "", "", 0.0, 0.0, 1.0, 0.0, 0.0, 0)
    count = len(items)
    correct = sum(1 for item in items if bool(item.get("direction_correct")))
    mape = sum(float(item.get("mape") or 0.0) for item in items) / count
    predicted = [float(item.get("predicted_move_pct") or 0.0) for item in items]
    realized = [float(item.get("realized_move_pct") or 0.0) for item in items]
    probabilities = [float(item.get("probability_up") or 0.0) for item in items]
    realized_up = [1.0 if value > 0 else 0.0 for value in realized]
    calibration = abs((sum(probabilities) / count) - (sum(realized_up) / count))
    first = items[0]
    return ForecastValidationSummary(
        symbol=normalize_symbol(first.get("symbol")),
        asset_class=str(first.get("asset_class") or ""),
        source_interval=str(first.get("source_interval") or ""),
        model=str(first.get("model") or ""),
        model_version=str(first.get("model_version") or ""),
        directional_accuracy=correct / count,
        mean_absolute_percentage_error=mape,
        calibration_error=calibration,
        average_predicted_move=sum(predicted) / count,
        average_realized_move=sum(realized) / count,
        sample_count=count,
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
    summary = validation_summary(symbol, asset_class, source_interval, model, model_version)
    if summary.sample_count < FORECAST_MIN_VALIDATION_SAMPLES:
        return False, f"forecast model has {summary.sample_count} validation samples; needs {FORECAST_MIN_VALIDATION_SAMPLES}"
    if summary.directional_accuracy < FORECAST_MIN_DIRECTIONAL_ACCURACY:
        return False, f"forecast directional accuracy {summary.directional_accuracy:.2f} is below {FORECAST_MIN_DIRECTIONAL_ACCURACY:.2f}"
    if summary.calibration_error > FORECAST_MAX_CALIBRATION_ERROR:
        return False, f"forecast probability calibration error {summary.calibration_error:.2f} exceeds {FORECAST_MAX_CALIBRATION_ERROR:.2f}"
    return True, "forecast validation approved"
