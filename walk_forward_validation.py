from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import os
import uuid
from typing import Any

import numpy as np
import pandas as pd

from database import connect, utc_now
from forecast_calibration import evaluate_probability_calibration
from forecasting import forecast_price


@dataclass(frozen=True)
class WalkForwardResult:
    run_id: str
    model: str
    model_version: str
    symbol: str
    market: str
    asset_class: str
    source_interval: str
    horizon_bars: int
    minimum_history_bars: int
    sample_count: int
    fold_count: int
    metrics: dict[str, Any]
    regime_metrics: dict[str, Any]
    benchmarks: dict[str, Any]
    leakage_checks: dict[str, Any]
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _close(history: pd.DataFrame) -> pd.Series:
    if "Close" not in history.columns:
        return pd.Series(dtype=float)
    series = history["Close"]
    if isinstance(series, pd.DataFrame):
        series = series.iloc[:, -1]
    return pd.to_numeric(series, errors="coerce")


def _regime_from_past(close: pd.Series) -> str:
    values = close.dropna()
    if len(values) < 30:
        return "unknown"
    returns = values.pct_change().dropna()
    recent = returns.tail(20)
    context = returns.tail(min(120, len(returns)))
    if recent.empty or context.empty:
        return "unknown"
    recent_vol = _finite(recent.std())
    context_vol = _finite(context.std())
    recent_mean = _finite(recent.mean())
    if context_vol > 0 and recent_vol >= context_vol * 1.5:
        return "high_volatility"
    threshold = max(context_vol * 0.15, 0.0005)
    if recent_mean > threshold:
        return "risk_on"
    if recent_mean < -threshold:
        return "risk_off"
    return "neutral"


def _baseline_probabilities(past_close: pd.Series) -> dict[str, float]:
    values = past_close.dropna()
    returns = values.pct_change().dropna()
    if returns.empty:
        return {"coin_flip": 0.5, "base_rate": 0.5, "previous_direction": 0.5, "momentum_5": 0.5}
    recent_for_rate = returns.tail(min(120, len(returns)))
    base_rate = float((recent_for_rate > 0).mean()) if len(recent_for_rate) else 0.5
    previous_direction = 0.60 if float(returns.iloc[-1]) > 0 else 0.40
    momentum = _finite(values.pct_change(5).iloc[-1]) if len(values) >= 6 else 0.0
    momentum_probability = 0.65 if momentum > 0 else (0.35 if momentum < 0 else 0.5)
    return {
        "coin_flip": 0.5,
        "base_rate": min(0.95, max(0.05, base_rate)),
        "previous_direction": previous_direction,
        "momentum_5": momentum_probability,
    }


def _metric_payload(records: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = evaluate_probability_calibration(records, bins=10)
    return metrics.to_dict()


def _benchmark_payload(records: list[dict[str, Any]], field: str) -> dict[str, Any]:
    converted = [
        {
            "probability_up": item.get(field),
            "realized_move_pct": item.get("realized_move_pct"),
        }
        for item in records
    ]
    return _metric_payload(converted)


def temporal_leakage_probe(
    history: pd.DataFrame,
    *,
    decision_position: int,
    horizon_bars: int = 5,
    source_interval: str = "1d",
    asset_class: str = "stock",
    market: str = "cash",
) -> dict[str, Any]:
    """Adversarially mutate future bars and prove the past forecast is unchanged."""
    if history is None or history.empty or decision_position < 40 or decision_position >= len(history) - 1:
        return {"ok": False, "reason": "insufficient probe data"}
    past = history.iloc[: decision_position + 1].copy()
    original = forecast_price(
        past,
        source_interval=source_interval,
        horizon_bars=horizon_bars,
        asset_class=asset_class,
        market=market,
    )
    if original is None:
        return {"ok": False, "reason": "forecast unavailable for probe"}

    mutated = history.copy(deep=True)
    future_start = decision_position + 1
    for column in ("Open", "High", "Low", "Close"):
        if column in mutated.columns:
            mutated.iloc[future_start:, mutated.columns.get_loc(column)] = (
                pd.to_numeric(mutated.iloc[future_start:][column], errors="coerce").fillna(1.0).to_numpy() * 1000.0 + 12345.0
            )
    mutated_past = mutated.iloc[: decision_position + 1].copy()
    after = forecast_price(
        mutated_past,
        source_interval=source_interval,
        horizon_bars=horizon_bars,
        asset_class=asset_class,
        market=market,
    )
    if after is None:
        return {"ok": False, "reason": "mutated forecast unavailable"}
    fields = ("target_price", "low_price", "high_price", "probability_up", "expected_move_pct", "spot_price")
    differences = {
        field: abs(_finite(getattr(original, field)) - _finite(getattr(after, field)))
        for field in fields
    }
    return {
        "ok": all(value <= 1e-12 for value in differences.values()),
        "decision_position": decision_position,
        "future_start_position": future_start,
        "differences": differences,
    }


def evaluate_forecast_walk_forward(
    symbol: str,
    history: pd.DataFrame,
    *,
    source_interval: str = "1d",
    asset_class: str | None = None,
    market: str | None = None,
    horizon_bars: int = 5,
    minimum_history_bars: int = 60,
    stride: int = 1,
    model: str = "log-return diffusion",
    model_version: str = "v36-timeframe-aware",
    persist: bool = False,
) -> WalkForwardResult:
    """Chronological out-of-sample validation with no shuffled observations.

    Every forecast only receives bars at or before its decision position. The
    realized outcome is then taken strictly from a later bar. Baselines are
    generated from the same past-only window.
    """
    if history is None or history.empty or "Close" not in history.columns:
        raise ValueError("walk-forward validation requires OHLC history with Close")
    horizon_bars = max(1, int(horizon_bars))
    minimum_history_bars = max(40, int(minimum_history_bars))
    stride = max(1, int(stride))
    if len(history) <= minimum_history_bars + horizon_bars:
        raise ValueError("insufficient history for walk-forward validation")

    symbol = str(symbol or "").upper().strip()
    asset = str(asset_class or ("crypto" if symbol.endswith("-USD") else "stock")).lower()
    market_name = str(market or ("crypto" if asset == "crypto" else "cash")).lower()
    close = _close(history)
    records: list[dict[str, Any]] = []
    ordering_violations = 0

    for decision_position in range(minimum_history_bars - 1, len(history) - horizon_bars, stride):
        outcome_position = decision_position + horizon_bars
        past = history.iloc[: decision_position + 1].copy()
        forecast = forecast_price(
            past,
            source_interval=source_interval,
            horizon_bars=horizon_bars,
            asset_class=asset,
            market=market_name,
            model=model,
            model_version=model_version,
        )
        if forecast is None:
            continue
        decision_price = _finite(close.iloc[decision_position])
        outcome_price = _finite(close.iloc[outcome_position])
        if decision_price <= 0 or outcome_price <= 0:
            continue
        if outcome_position <= decision_position:
            ordering_violations += 1
            continue
        realized_move_pct = (outcome_price / decision_price - 1.0) * 100.0
        baseline = _baseline_probabilities(close.iloc[: decision_position + 1])
        records.append(
            {
                "decision_position": decision_position,
                "outcome_position": outcome_position,
                "decision_timestamp": str(history.index[decision_position]),
                "outcome_timestamp": str(history.index[outcome_position]),
                "probability_up": float(forecast.probability_up),
                "predicted_move_pct": float(forecast.expected_move_pct),
                "realized_move_pct": realized_move_pct,
                "regime": _regime_from_past(close.iloc[: decision_position + 1]),
                **{f"baseline_{name}": value for name, value in baseline.items()},
            }
        )

    metrics = _metric_payload(records)
    benchmarks = {
        name: _benchmark_payload(records, f"baseline_{name}")
        for name in ("coin_flip", "base_rate", "previous_direction", "momentum_5")
    }
    regime_metrics: dict[str, Any] = {}
    for regime in sorted({str(item.get("regime") or "unknown") for item in records}):
        subset = [item for item in records if item.get("regime") == regime]
        regime_metrics[regime] = _metric_payload(subset)

    probe_position = max(minimum_history_bars, len(history) - horizon_bars - 2)
    probe_position = min(probe_position, len(history) - 2)
    probe = temporal_leakage_probe(
        history,
        decision_position=probe_position,
        horizon_bars=horizon_bars,
        source_interval=source_interval,
        asset_class=asset,
        market=market_name,
    )
    leakage = {
        "strict_ordering": ordering_violations == 0,
        "ordering_violations": ordering_violations,
        "future_mutation_probe": probe,
    }

    sample_count = int(metrics.get("sample_count") or 0)
    min_samples = max(30, int(os.getenv("CAPITAL_MIN_WALK_FORWARD_SAMPLES", "100")))
    max_ece = max(0.0, min(1.0, float(os.getenv("CAPITAL_MAX_ECE", "0.12"))))
    min_brier_skill = float(os.getenv("CAPITAL_MIN_BRIER_SKILL", "0.02"))
    min_accuracy = max(0.0, min(1.0, float(os.getenv("CAPITAL_MIN_DIRECTIONAL_ACCURACY", "0.52"))))
    oracle_brier = metrics.get("brier_score")
    best_baseline_brier = min(
        [float(item["brier_score"]) for item in benchmarks.values() if item.get("brier_score") is not None],
        default=None,
    )
    beats_all_baselines = (
        oracle_brier is not None
        and best_baseline_brier is not None
        and float(oracle_brier) < float(best_baseline_brier)
    )
    regime_counts_ok = all(
        int(item.get("sample_count") or 0) >= 10
        for name, item in regime_metrics.items()
        if name != "unknown"
    ) if regime_metrics else False
    passed = all(
        [
            sample_count >= min_samples,
            bool(leakage["strict_ordering"]),
            bool(probe.get("ok")),
            metrics.get("expected_calibration_error") is not None and float(metrics["expected_calibration_error"]) <= max_ece,
            metrics.get("brier_skill_score") is not None and float(metrics["brier_skill_score"]) >= min_brier_skill,
            metrics.get("directional_accuracy") is not None and float(metrics["directional_accuracy"]) >= min_accuracy,
            beats_all_baselines,
            regime_counts_ok,
        ]
    )
    status = "PASS" if passed else "INSUFFICIENT_OR_FAILED_EVIDENCE"
    run_id = f"wf:{uuid.uuid4()}"
    result = WalkForwardResult(
        run_id=run_id,
        model=model,
        model_version=model_version,
        symbol=symbol,
        market=market_name,
        asset_class=asset,
        source_interval=source_interval,
        horizon_bars=horizon_bars,
        minimum_history_bars=minimum_history_bars,
        sample_count=sample_count,
        fold_count=max(1, math.ceil(sample_count / 50)) if sample_count else 0,
        metrics={**metrics, "beats_all_baselines": beats_all_baselines},
        regime_metrics=regime_metrics,
        benchmarks=benchmarks,
        leakage_checks=leakage,
        status=status,
    )
    if persist:
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO walk_forward_validation_runs (
                    run_id, model, model_version, market, asset_class, symbol,
                    source_interval, horizon_bars, minimum_history_bars,
                    sample_count, fold_count, metrics, regime_metrics, benchmarks,
                    leakage_checks, status, created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s)
                """,
                (
                    result.run_id,
                    result.model,
                    result.model_version,
                    result.market,
                    result.asset_class,
                    result.symbol,
                    result.source_interval,
                    result.horizon_bars,
                    result.minimum_history_bars,
                    result.sample_count,
                    result.fold_count,
                    json.dumps(result.metrics, sort_keys=True, default=str),
                    json.dumps(result.regime_metrics, sort_keys=True, default=str),
                    json.dumps(result.benchmarks, sort_keys=True, default=str),
                    json.dumps(result.leakage_checks, sort_keys=True, default=str),
                    result.status,
                    utc_now(),
                ),
            )
    return result
