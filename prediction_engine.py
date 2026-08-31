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
from probability_evidence import probability_metadata


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


def _age_minutes(value: Any, reference: datetime | None = None) -> float | None:
    parsed = _timestamp(value)
    if parsed is None:
        return None
    reference = reference or datetime.now(timezone.utc)
    return max(0.0, (reference - parsed).total_seconds() / 60.0)


def _known_at(record: dict[str, Any], as_of: datetime | None, fields: tuple[str, ...]) -> bool:
    if as_of is None:
        return True
    for field in fields:
        if field not in record or record.get(field) in (None, ""):
            continue
        parsed = _timestamp(record.get(field))
        if parsed is not None and parsed > as_of:
            return False
    return True


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
    reference_time: datetime | None = None,
) -> tuple[str, bool, str, float | None, float | None]:
    """Return final action, execution-readiness, plain status, and age.

    This gate prevents stale opportunity-ranking records from appearing as
    current BUY recommendations. It does not fabricate missing prices or targets.
    """
    signal_age = _age_minutes(signal_time, reference_time)
    forecast_age = _age_minutes(forecast_time, reference_time)
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
    decision_timestamp: Any | None = None,
) -> list[dict[str, Any]]:
    as_of = _timestamp(decision_timestamp)
    latest_signal: dict[tuple[str, str], dict[str, Any]] = {}
    latest_forecasts: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in signals:
        enriched = dict(item)
        details = _payload(enriched.get("details"))
        route = _payload(details.get("market_data_route"))
        if not _known_at(enriched, as_of, ("created_at", "timestamp", "quote_timestamp", "source_quote_timestamp", "fetched_at")):
            continue
        if not _known_at(route, as_of, ("quote_timestamp", "timestamp", "fetched_at", "provider_fetched_at")):
            continue
        for field in (
            "scan_type",
            "source_interval",
            "source_quote_timestamp",
            "quote_timestamp",
            "quote_age_seconds",
            "requested_symbol",
            "provider_symbol",
            "provider",
            "quote_verified",
        ):
            if enriched.get(field) in (None, ""):
                enriched[field] = details.get(field, route.get(field))
        if enriched.get("source_interval") in (None, ""):
            enriched["source_interval"] = route.get("interval")
        key = (str(enriched.get("market", "cash")), str(enriched.get("symbol", "")).upper())
        existing = latest_signal.get(key)
        if existing is None or (_timestamp(enriched.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)) > (_timestamp(existing.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)):
            latest_signal[key] = enriched
    for item in forecasts:
        item = dict(item)
        if not _known_at(item, as_of, ("created_at", "source_quote_timestamp", "quote_timestamp", "fetched_at")):
            continue
        key = (str(item.get("market", "cash")), str(item.get("symbol", "")).upper())
        latest_forecasts.setdefault(key, []).append(item)

    def matching_forecast(key: tuple[str, str], signal: dict[str, Any]) -> dict[str, Any]:
        candidates = latest_forecasts.get(key, [])
        if not candidates:
            return {}
        signal_interval = str(signal.get("source_interval") or signal.get("interval") or "").strip()
        signal_scan = str(signal.get("scan_type") or "").strip()
        signal_quote_time = signal.get("source_quote_timestamp") or signal.get("quote_timestamp")
        requested = str(signal.get("symbol") or key[1]).upper()
        for candidate in candidates:
            forecast_interval = str(candidate.get("source_interval") or "").strip()
            if forecast_interval and signal_interval and forecast_interval != signal_interval:
                continue
            forecast_scan = str(candidate.get("scan_type") or "").strip()
            if forecast_scan and signal_scan and forecast_scan != signal_scan:
                continue
            forecast_quote = candidate.get("source_quote_timestamp")
            if forecast_quote and signal_quote_time:
                left = _timestamp(forecast_quote)
                right = _timestamp(signal_quote_time)
                if left is None or right is None or abs((left - right).total_seconds()) > 1:
                    continue
            if str(candidate.get("market") or key[0]).lower() != key[0]:
                continue
            if str(candidate.get("requested_symbol") or "").upper() != requested:
                continue
            if str(candidate.get("provider_symbol") or "").upper() != requested:
                continue
            if str(candidate.get("currency") or signal.get("currency") or "").upper() != str(signal.get("currency") or candidate.get("currency") or "").upper():
                continue
            if str(candidate.get("exchange") or signal.get("exchange") or "").upper() != str(signal.get("exchange") or candidate.get("exchange") or "").upper():
                continue
            if signal.get("strategy_horizon") and candidate.get("strategy_horizon") and str(signal.get("strategy_horizon")) != str(candidate.get("strategy_horizon")):
                continue
            return candidate
        return {}

    results: list[dict[str, Any]] = []
    for op in opportunities:
        if not _known_at(op, as_of, ("created_at", "timestamp", "fetched_at")):
            continue
        market = str(op.get("market", "cash")).lower()
        symbol = str(op.get("symbol", "")).upper()
        score = _f(op.get("opportunity_score"))
        sig = latest_signal.get((market, symbol), {})
        fc = matching_forecast((market, symbol), sig)
        payload = _payload(op.get("payload"))
        payload_route = _payload(payload.get("market_data_route"))
        confidence = _f(sig.get("confidence"), _f(payload.get("confidence"), score))
        if confidence <= 1:
            confidence *= 100
        price = _f(sig.get("price"), _f(payload.get("price"), _f(payload_route.get("price"))))
        target = _f(fc.get("target_price"), _f(payload.get("target_price")))
        low = _f(fc.get("low_price"), _f(payload.get("low_price")))
        high = _f(fc.get("high_price"), _f(payload.get("high_price")))
        raw_probability_up = fc.get("probability_up")
        if raw_probability_up is not None:
            prob_up = _f(raw_probability_up)
            if prob_up <= 1:
                prob_up *= 100
            probability_info = probability_metadata(
                field_name="probability_up",
                value=raw_probability_up,
                source="prediction_engine.forecast",
                calibrated=False,
                model_backed=bool(fc),
                sample_count=fc.get("validation_sample_count") or 0,
                model=str(fc.get("model") or ""),
                model_version=str(fc.get("model_version") or ""),
                notes="Forecast probability_up is a model estimate unless linked calibration evidence explicitly upgrades it.",
            )
        else:
            prob_up = None
            probability_info = probability_metadata(
                field_name="confidence",
                value=confidence,
                source="prediction_engine.signal_confidence",
                calibrated=False,
                model_backed=False,
                sample_count=0,
                notes="No forecast probability_up was supplied; signal confidence remains a heuristic score.",
            )
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
            reference_time=as_of,
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
            "probability_up": round(max(0.0, min(100.0, prob_up)), 1) if prob_up is not None else None,
            "probability_evidence": probability_info,
            "expected_return": round(expected, 1),
            "risk": risk,
            "reason": reason,
            "created_at": signal_time,
            "scan_type": sig.get("scan_type") or payload.get("scan_type"),
            "source_interval": sig.get("source_interval") or payload.get("source_interval") or payload_route.get("interval"),
            "source_quote_timestamp": sig.get("source_quote_timestamp") or payload.get("source_quote_timestamp") or payload_route.get("quote_timestamp"),
            "quote_timestamp": sig.get("quote_timestamp") or payload.get("quote_timestamp") or payload_route.get("quote_timestamp"),
            "quote_age_seconds": sig.get("quote_age_seconds") if sig.get("quote_age_seconds") is not None else payload.get("quote_age_seconds"),
            "requested_symbol": sig.get("requested_symbol") or payload.get("requested_symbol") or payload_route.get("requested_symbol"),
            "provider_symbol": sig.get("provider_symbol") or payload.get("provider_symbol") or payload_route.get("provider_symbol"),
            "provider": sig.get("provider") or payload.get("provider") or payload_route.get("provider"),
            "quote_verified": bool(sig.get("quote_verified") or payload.get("quote_verified") or payload_route.get("quote_verified") is True),
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
