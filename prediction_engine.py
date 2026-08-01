from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Iterable

from config import (
    DECISION_CRYPTO_MAX_AGE_MINUTES,
    DECISION_STOCK_MAX_AGE_MINUTES,
    MIN_ACTIONABLE_MOVE_CRYPTO_PCT,
    MIN_ACTIONABLE_MOVE_STOCK_PCT,
    REQUIRE_TARGET_FOR_BUY,
)


def _f(v: Any, default: float = 0.0) -> float:
    try:
        value = float(v)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
            return loaded if isinstance(loaded, dict) else {}
        except Exception:
            return {}
    return {}


def _positive(value: Any) -> bool:
    number = _f(value)
    return math.isfinite(number) and number > 0.0


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_minutes(value: Any) -> float | None:
    parsed = _timestamp(value)
    if parsed is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 60.0)


def normalize_action(action: Any, score: float = 0.0) -> str:
    text = str(action or "").upper()
    if "SELL" in text or "EXIT" in text:
        return "SELL"
    if "BUY" in text:
        return "BUY"
    if "HOLD" in text:
        return "HOLD"
    if score >= 82:
        return "BUY"
    if score <= 30:
        return "SELL"
    return "WAIT"


def _data_gate(
    *,
    market: str,
    requested_action: str,
    price: float,
    target: float,
    expected_return: float,
    signal_time: Any,
    forecast_time: Any,
) -> tuple[str, bool, str, float | None, float | None]:
    """Return final action, execution-readiness, plain status, and age.

    This gate prevents stale opportunity-ranking records from appearing as
    current BUY recommendations. It does not fabricate missing prices or targets.
    """
    signal_age = _age_minutes(signal_time)
    forecast_age = _age_minutes(forecast_time)
    max_age = DECISION_CRYPTO_MAX_AGE_MINUTES if market == "crypto" else DECISION_STOCK_MAX_AGE_MINUTES
    min_move = MIN_ACTIONABLE_MOVE_CRYPTO_PCT if market == "crypto" else MIN_ACTIONABLE_MOVE_STOCK_PCT

    if not _positive(price):
        return "WAIT", False, "Waiting for a live market price", signal_age, forecast_age
    if signal_age is None:
        return "WAIT", False, "Waiting for a valid live signal timestamp", signal_age, forecast_age
    if signal_age > max_age:
        return "WAIT", False, f"Market signal is stale ({signal_age:.0f} minutes old)", signal_age, forecast_age

    if requested_action == "BUY":
        if REQUIRE_TARGET_FOR_BUY and not _positive(target):
            return "WAIT", False, "Waiting for a current forecast target", signal_age, forecast_age
        if forecast_age is None:
            return "WAIT", False, "Waiting for a valid forecast timestamp", signal_age, forecast_age
        if forecast_age > max_age:
            return "WAIT", False, f"Forecast is stale ({forecast_age:.0f} minutes old)", signal_age, forecast_age
        if _positive(target) and target <= price:
            return "WAIT", False, "Forecast does not currently offer upside", signal_age, forecast_age
        if expected_return < min_move:
            return "WAIT", False, f"Expected move is below the {min_move:.2f}% trade threshold", signal_age, forecast_age
        return "BUY", True, "Live quote and forecast passed the trade-readiness checks", signal_age, forecast_age

    if requested_action == "SELL":
        return "SELL", True, "Live price is available for risk review", signal_age, forecast_age
    if requested_action == "HOLD":
        return "HOLD", True, "Live price is available; no new entry is approved", signal_age, forecast_age
    return "WAIT", True, "Live price is available; stronger confirmation is required", signal_age, forecast_age


def build_decisions(
    opportunities: Iterable[dict[str, Any]],
    signals: Iterable[dict[str, Any]],
    forecasts: Iterable[dict[str, Any]],
    limit: int = 30,
) -> list[dict[str, Any]]:
    latest_signal: dict[tuple[str, str], dict[str, Any]] = {}
    latest_forecast: dict[tuple[str, str], dict[str, Any]] = {}
    for item in signals:
        key = (str(item.get("market", "cash")), str(item.get("symbol", "")).upper())
        latest_signal.setdefault(key, item)
    for item in forecasts:
        key = (str(item.get("market", "cash")), str(item.get("symbol", "")).upper())
        latest_forecast.setdefault(key, item)

    results: list[dict[str, Any]] = []
    for op in opportunities:
        market = str(op.get("market", "cash")).lower()
        symbol = str(op.get("symbol", "")).upper()
        score = _f(op.get("opportunity_score"))
        sig = latest_signal.get((market, symbol), {})
        fc = latest_forecast.get((market, symbol), {})
        payload = _payload(op.get("payload"))
        confidence = _f(sig.get("confidence"), _f(payload.get("confidence"), score))
        if confidence <= 1:
            confidence *= 100
        price = _f(sig.get("price"), _f(payload.get("price")))
        target = _f(fc.get("target_price"), _f(payload.get("target_price")))
        low = _f(fc.get("low_price"), _f(payload.get("low_price")))
        high = _f(fc.get("high_price"), _f(payload.get("high_price")))
        prob_up = _f(fc.get("probability_up"), confidence)
        if prob_up <= 1:
            prob_up *= 100
        expected = ((target / price) - 1) * 100 if _positive(price) and _positive(target) else _f(payload.get("expected_return"))
        requested_action = normalize_action(sig.get("action") or payload.get("action"), score)
        signal_time = sig.get("created_at")
        forecast_time = fc.get("created_at")
        action, trade_eligible, data_status, signal_age_minutes, forecast_age_minutes = _data_gate(
            market=market,
            requested_action=requested_action,
            price=price,
            target=target,
            expected_return=expected,
            signal_time=signal_time,
            forecast_time=forecast_time,
        )
        details = _payload(sig.get("details"))
        reason = str(payload.get("reason") or details.get("reason") or sig.get("details") or "Ranked by the Oracle's combined market evidence.")
        reason = reason[:280]
        risk = "Low" if score >= 85 and confidence >= 80 else "Moderate" if score >= 60 else "High"
        results.append({
            "market": market,
            "symbol": symbol,
            "action": action,
            "requested_action": requested_action,
            "trade_eligible": trade_eligible,
            "data_status": data_status,
            "data_age_minutes": round(signal_age_minutes, 1) if signal_age_minutes is not None else None,
            "signal_age_minutes": round(signal_age_minutes, 1) if signal_age_minutes is not None else None,
            "forecast_age_minutes": round(forecast_age_minutes, 1) if forecast_age_minutes is not None else None,
            "score": round(score, 1),
            "confidence": round(max(0.0, min(100.0, confidence)), 1),
            "price": price,
            "target": target,
            "low": low,
            "high": high,
            "probability_up": round(max(0.0, min(100.0, prob_up)), 1),
            "expected_return": round(expected, 1),
            "risk": risk,
            "reason": reason,
            "created_at": signal_time,
        })

    # Trade-ready BUYs first, then other current decisions, with incomplete/stale
    # records kept at the bottom for transparency rather than silently discarded.
    return sorted(
        results,
        key=lambda x: (
            bool(x.get("trade_eligible")),
            x.get("action") == "BUY",
            x.get("action") == "SELL",
            _f(x.get("score")),
            _f(x.get("confidence")),
        ),
        reverse=True,
    )[:limit]
