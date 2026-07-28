from __future__ import annotations

import json
from typing import Any, Iterable


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
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
        market = str(op.get("market", "cash"))
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
        expected = ((target / price) - 1) * 100 if price and target else _f(payload.get("expected_return"))
        action = normalize_action(sig.get("action") or payload.get("action"), score)
        details = _payload(sig.get("details"))
        reason = str(payload.get("reason") or details.get("reason") or sig.get("details") or "Ranked by the Oracle's combined market evidence.")
        reason = reason[:280]
        risk = "Low" if score >= 85 and confidence >= 80 else "Moderate" if score >= 60 else "High"
        results.append({
            "market": market, "symbol": symbol, "action": action, "score": round(score, 1),
            "confidence": round(max(0.0, min(100.0, confidence)), 1), "price": price,
            "target": target, "low": low, "high": high, "probability_up": round(max(0.0, min(100.0, prob_up)), 1),
            "expected_return": round(expected, 1), "risk": risk, "reason": reason,
            "created_at": op.get("created_at") or sig.get("created_at"),
        })
    return sorted(results, key=lambda x: (x["action"] == "BUY", x["score"], x["confidence"]), reverse=True)[:limit]
