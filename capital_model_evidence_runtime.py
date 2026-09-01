from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import math
import os
import uuid
from typing import Any

from capital_model_governance import apply_model_governance
from config import FORECAST_MODEL_VERSION
from database import connect, row, rows, utc_now
from forecast_calibration import evaluate_probability_calibration
from forecasting import forecast_price
from market_data import get_history, history_matches_symbol
from walk_forward_validation import _baseline_probabilities, _close, _regime_from_past, temporal_leakage_probe


log = logging.getLogger("capital-model-evidence-runtime")


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _int_env(name: str, default: int, minimum: int, maximum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _symbols() -> list[str]:
    raw = os.getenv("CAPITAL_WALK_FORWARD_SYMBOLS", "BTC-USD,ETH-USD,SOL-USD")
    result: list[str] = []
    for item in str(raw or "").split(","):
        symbol = item.strip().upper()
        if symbol and symbol not in result:
            result.append(symbol)
    return result[:12] or ["BTC-USD", "ETH-USD", "SOL-USD"]


def _execution_models() -> list[tuple[str, str]]:
    try:
        records = rows(
            """
            SELECT model, COALESCE(model_version,'') AS model_version, MAX(created_at) AS latest
            FROM forecasts
            WHERE model IS NOT NULL AND model <> ''
            GROUP BY model, COALESCE(model_version,'')
            ORDER BY latest DESC
            LIMIT 3
            """
        )
    except Exception:
        records = []
    models = [
        (str(item.get("model") or ""), str(item.get("model_version") or ""))
        for item in records
        if item.get("model")
    ]
    return models or [("log-return diffusion", FORECAST_MODEL_VERSION)]


def _recent_evidence(model: str, model_version: str) -> dict[str, int]:
    max_age_hours = _int_env("CAPITAL_MODEL_REFRESH_HOURS", 12, 1, 168)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    try:
        wf = row(
            """
            SELECT COUNT(*)::int AS run_count,
                   COUNT(DISTINCT symbol)::int AS distinct_symbols
            FROM walk_forward_validation_runs
            WHERE model=%s AND COALESCE(model_version,'')=COALESCE(%s,'')
              AND created_at >= %s
            """,
            (model, model_version, cutoff.isoformat()),
        ) or {}
        calibration = row(
            """
            SELECT COUNT(*)::int AS sample_count
            FROM forecast_validation
            WHERE model=%s AND COALESCE(model_version,'')=COALESCE(%s,'')
            """,
            (model, model_version),
        ) or {}
    except Exception:
        return {"run_count": 0, "distinct_symbols": 0, "calibration_samples": 0}
    return {
        "run_count": int(wf.get("run_count") or 0),
        "distinct_symbols": int(wf.get("distinct_symbols") or 0),
        "calibration_samples": int(calibration.get("sample_count") or 0),
    }


def _evidence_current(model: str, model_version: str) -> bool:
    evidence = _recent_evidence(model, model_version)
    min_symbols = max(3, _int_env("CAPITAL_MIN_MODEL_SYMBOLS", 3, 2, 12))
    min_samples = max(30, _int_env("CAPITAL_MIN_WALK_FORWARD_SAMPLES", 100, 30, 5000))
    return (
        evidence["run_count"] >= min_symbols
        and evidence["distinct_symbols"] >= min_symbols
        and evidence["calibration_samples"] >= min_samples
    )


def _validation_key(
    *,
    model: str,
    model_version: str,
    symbol: str,
    interval: str,
    horizon_bars: int,
    decision_timestamp: str,
    outcome_timestamp: str,
) -> str:
    raw = "|".join(
        [model, model_version, symbol, interval, str(horizon_bars), decision_timestamp, outcome_timestamp]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _calibration_payload(records: list[dict[str, Any]]) -> dict[str, Any]:
    return evaluate_probability_calibration(records, bins=10).to_dict()


def _benchmark_payload(records: list[dict[str, Any]], field: str) -> dict[str, Any]:
    return _calibration_payload(
        [
            {
                "probability_up": item.get(field),
                "realized_move_pct": item.get("realized_move_pct"),
            }
            for item in records
        ]
    )


def _build_symbol_evidence(
    symbol: str,
    model: str,
    model_version: str,
    *,
    period: str,
    interval: str,
    horizon_bars: int,
    minimum_history_bars: int,
    stride: int,
) -> dict[str, Any]:
    history = get_history(symbol, period=period, interval=interval)
    if history is None or history.empty or not history_matches_symbol(history, symbol) or "Close" not in history.columns:
        return {"symbol": symbol, "status": "HISTORY_UNAVAILABLE", "sample_count": 0}
    if len(history) <= minimum_history_bars + horizon_bars:
        return {"symbol": symbol, "status": "INSUFFICIENT_HISTORY", "sample_count": 0}

    close = _close(history)
    records: list[dict[str, Any]] = []
    ordering_violations = 0
    asset_class = "crypto" if symbol.endswith("-USD") else "stock"
    market = "crypto" if asset_class == "crypto" else "cash"

    for decision_position in range(minimum_history_bars - 1, len(history) - horizon_bars, stride):
        outcome_position = decision_position + horizon_bars
        if outcome_position <= decision_position:
            ordering_violations += 1
            continue
        past = history.iloc[: decision_position + 1].copy()
        try:
            forecast = forecast_price(
                past,
                source_interval=interval,
                horizon_bars=horizon_bars,
                asset_class=asset_class,
                market=market,
                model=model,
                model_version=model_version,
            )
        except Exception:
            forecast = None
        if forecast is None:
            continue

        decision_price = _finite(close.iloc[decision_position])
        outcome_price = _finite(close.iloc[outcome_position])
        if decision_price <= 0 or outcome_price <= 0:
            continue
        realized_move_pct = (outcome_price / decision_price - 1.0) * 100.0
        predicted_move_pct = _finite(getattr(forecast, "expected_move_pct", None))
        probability_up = _finite(getattr(forecast, "probability_up", None), 0.5)
        probability_up = min(1.0, max(0.0, probability_up))
        target_price = _finite(getattr(forecast, "target_price", None))
        mape = abs(target_price - outcome_price) / outcome_price * 100.0 if target_price > 0 else abs(predicted_move_pct - realized_move_pct)
        baseline = _baseline_probabilities(close.iloc[: decision_position + 1])
        decision_timestamp = str(history.index[decision_position])
        outcome_timestamp = str(history.index[outcome_position])
        records.append(
            {
                "decision_position": decision_position,
                "outcome_position": outcome_position,
                "decision_timestamp": decision_timestamp,
                "outcome_timestamp": outcome_timestamp,
                "probability_up": probability_up,
                "predicted_move_pct": predicted_move_pct,
                "realized_move_pct": realized_move_pct,
                "direction_correct": (probability_up >= 0.5) == (realized_move_pct > 0.0),
                "mape": mape,
                "regime": _regime_from_past(close.iloc[: decision_position + 1]),
                **{f"baseline_{name}": value for name, value in baseline.items()},
            }
        )

    metrics = _calibration_payload(records)
    benchmarks = {
        name: _benchmark_payload(records, f"baseline_{name}")
        for name in ("coin_flip", "base_rate", "previous_direction", "momentum_5")
    }
    regime_metrics: dict[str, Any] = {}
    for regime in sorted({str(item.get("regime") or "unknown") for item in records}):
        subset = [item for item in records if item.get("regime") == regime]
        regime_metrics[regime] = _calibration_payload(subset)

    probe_position = max(minimum_history_bars, len(history) - horizon_bars - 2)
    probe_position = min(probe_position, len(history) - 2)
    probe = temporal_leakage_probe(
        history,
        decision_position=probe_position,
        horizon_bars=horizon_bars,
        source_interval=interval,
        asset_class=asset_class,
        market=market,
    )
    leakage = {
        "strict_ordering": ordering_violations == 0,
        "ordering_violations": ordering_violations,
        "future_mutation_probe": probe,
    }

    sample_count = int(metrics.get("sample_count") or 0)
    min_samples = max(30, _int_env("CAPITAL_MIN_WALK_FORWARD_SAMPLES", 100, 30, 5000))
    max_ece = max(0.0, min(1.0, float(os.getenv("CAPITAL_MAX_ECE", "0.12"))))
    min_brier_skill = float(os.getenv("CAPITAL_MIN_BRIER_SKILL", "0.02"))
    min_accuracy = max(0.0, min(1.0, float(os.getenv("CAPITAL_MIN_DIRECTIONAL_ACCURACY", "0.52"))))
    oracle_brier = metrics.get("brier_score")
    baseline_briers = [
        float(item["brier_score"])
        for item in benchmarks.values()
        if item.get("brier_score") is not None
    ]
    best_baseline_brier = min(baseline_briers, default=None)
    beats_all_baselines = (
        oracle_brier is not None
        and best_baseline_brier is not None
        and float(oracle_brier) < float(best_baseline_brier)
    )
    regime_counts_ok = (
        all(
            int(item.get("sample_count") or 0) >= 10
            for name, item in regime_metrics.items()
            if name != "unknown"
        )
        if regime_metrics
        else False
    )
    passed = all(
        [
            sample_count >= min_samples,
            bool(leakage["strict_ordering"]),
            bool(probe.get("ok")),
            metrics.get("expected_calibration_error") is not None
            and float(metrics["expected_calibration_error"]) <= max_ece,
            metrics.get("brier_skill_score") is not None
            and float(metrics["brier_skill_score"]) >= min_brier_skill,
            metrics.get("directional_accuracy") is not None
            and float(metrics["directional_accuracy"]) >= min_accuracy,
            beats_all_baselines,
            regime_counts_ok,
        ]
    )
    status = "PASS" if passed else "INSUFFICIENT_OR_FAILED_EVIDENCE"
    run_id = f"wf:{uuid.uuid4()}"
    metrics_with_benchmark = {**metrics, "beats_all_baselines": beats_all_baselines}
    created_at = utc_now()

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
                run_id,
                model,
                model_version,
                market,
                asset_class,
                symbol,
                interval,
                horizon_bars,
                minimum_history_bars,
                sample_count,
                max(1, math.ceil(sample_count / 50)) if sample_count else 0,
                json.dumps(metrics_with_benchmark, sort_keys=True, default=str),
                json.dumps(regime_metrics, sort_keys=True, default=str),
                json.dumps(benchmarks, sort_keys=True, default=str),
                json.dumps(leakage, sort_keys=True, default=str),
                status,
                created_at,
            ),
        )
        for item in records:
            evidence_key = _validation_key(
                model=model,
                model_version=model_version,
                symbol=symbol,
                interval=interval,
                horizon_bars=horizon_bars,
                decision_timestamp=str(item["decision_timestamp"]),
                outcome_timestamp=str(item["outcome_timestamp"]),
            )
            conn.execute(
                """
                INSERT INTO forecast_validation (
                    symbol, asset_class, source_interval, model, model_version,
                    probability_up, predicted_move_pct, realized_move_pct,
                    direction_correct, mape, created_at, evidence_key,
                    decision_timestamp, outcome_timestamp, horizon_bars, run_id
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (evidence_key) DO NOTHING
                """,
                (
                    symbol,
                    asset_class,
                    interval,
                    model,
                    model_version,
                    item.get("probability_up"),
                    item.get("predicted_move_pct"),
                    item.get("realized_move_pct"),
                    bool(item.get("direction_correct")),
                    item.get("mape"),
                    created_at,
                    evidence_key,
                    item.get("decision_timestamp"),
                    item.get("outcome_timestamp"),
                    horizon_bars,
                    run_id,
                ),
            )

    return {
        "symbol": symbol,
        "status": status,
        "sample_count": sample_count,
        "directional_accuracy": metrics.get("directional_accuracy"),
        "expected_calibration_error": metrics.get("expected_calibration_error"),
        "brier_skill_score": metrics.get("brier_skill_score"),
        "beats_all_baselines": beats_all_baselines,
        "temporal_leakage_ok": bool(probe.get("ok")) and ordering_violations == 0,
        "run_id": run_id,
    }


def refresh_capital_model_evidence(*, force: bool = False) -> list[dict[str, Any]]:
    """Build real historical out-of-sample evidence for active execution models.

    This never changes strategy thresholds or manufactures successful metrics.
    It only generates the evidence that governance already requires. A weak model
    remains shadow/degraded and capital readiness remains fail-closed.
    """
    period = str(os.getenv("CAPITAL_WALK_FORWARD_PERIOD", "5y") or "5y")
    interval = str(os.getenv("CAPITAL_WALK_FORWARD_INTERVAL", "1d") or "1d")
    horizon_bars = _int_env("CAPITAL_WALK_FORWARD_HORIZON_BARS", 5, 1, 60)
    minimum_history_bars = _int_env("CAPITAL_WALK_FORWARD_MIN_HISTORY_BARS", 90, 40, 1000)
    stride = _int_env("CAPITAL_WALK_FORWARD_STRIDE", 5, 1, 50)
    symbols = _symbols()
    results: list[dict[str, Any]] = []

    for model, model_version in _execution_models():
        if not force and _evidence_current(model, model_version):
            assessment = apply_model_governance(model, model_version)
            results.append(
                {
                    "model": model,
                    "model_version": model_version,
                    "status": "CURRENT",
                    "governance_status": assessment.recommended_status,
                    "eligible_for_approval": assessment.eligible_for_approval,
                    **_recent_evidence(model, model_version),
                }
            )
            continue

        model_results: list[dict[str, Any]] = []
        for symbol in symbols:
            try:
                model_results.append(
                    _build_symbol_evidence(
                        symbol,
                        model,
                        model_version,
                        period=period,
                        interval=interval,
                        horizon_bars=horizon_bars,
                        minimum_history_bars=minimum_history_bars,
                        stride=stride,
                    )
                )
            except Exception as exc:
                model_results.append(
                    {"symbol": symbol, "status": "UNAVAILABLE", "reason": exc.__class__.__name__, "sample_count": 0}
                )
        assessment = apply_model_governance(model, model_version)
        result = {
            "model": model,
            "model_version": model_version,
            "status": "REFRESHED",
            "governance_status": assessment.recommended_status,
            "eligible_for_approval": assessment.eligible_for_approval,
            "symbols": model_results,
            **_recent_evidence(model, model_version),
        }
        results.append(result)
        log.info(
            "CAPITAL MODEL EVIDENCE | model=%s | version=%s | governance=%s | eligible=%s | runs=%s | symbols=%s | calibration_samples=%s",
            model,
            model_version,
            assessment.recommended_status,
            assessment.eligible_for_approval,
            result.get("run_count", 0),
            result.get("distinct_symbols", 0),
            result.get("calibration_samples", 0),
        )
        for item in model_results:
            log.info(
                "CAPITAL MODEL EVIDENCE SYMBOL | model=%s | symbol=%s | status=%s | samples=%s | accuracy=%s | ece=%s | brier_skill=%s | beats_baselines=%s | leakage=%s",
                model,
                item.get("symbol"),
                item.get("status"),
                item.get("sample_count", 0),
                item.get("directional_accuracy"),
                item.get("expected_calibration_error"),
                item.get("brier_skill_score"),
                item.get("beats_all_baselines"),
                item.get("temporal_leakage_ok"),
            )
    return results
