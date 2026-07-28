from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


MEMORY_MIN_ANALOGS = max(3, int(os.getenv("MEMORY_MIN_ANALOGS", "5")))
MEMORY_MAX_ADJUSTMENT = max(0.0, float(os.getenv("MEMORY_MAX_ADJUSTMENT", "8.0")))
MEMORY_LOOKBACK_LIMIT = max(25, int(os.getenv("MEMORY_LOOKBACK_LIMIT", "300")))
MEMORY_VETO_WIN_RATE = min(0.5, max(0.0, float(os.getenv("MEMORY_VETO_WIN_RATE", "0.30"))))
MEMORY_VETO_MIN_ANALOGS = max(MEMORY_MIN_ANALOGS, int(os.getenv("MEMORY_VETO_MIN_ANALOGS", "10")))


def _num(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return default if not math.isfinite(result) else result
    except (TypeError, ValueError):
        return default


def _value(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _normalized_score(value: Any) -> float:
    n = _num(value)
    return n * 100.0 if abs(n) <= 1.0 else n


def feature_vector(signal: Any) -> dict[str, float]:
    """Portable setup fingerprint used to compare current and historical trades."""
    return {
        "alpha": _normalized_score(_value(signal, "score", 50.0)) / 100.0,
        "confidence": _normalized_score(_value(signal, "confidence", 50.0)) / 100.0,
        "momentum_5d": _clip(_num(_value(signal, "momentum_5d", 0.0)), -0.30, 0.30) / 0.30,
        "momentum_20d": _clip(_num(_value(signal, "momentum_20d", 0.0)), -0.60, 0.60) / 0.60,
        "trend": _clip(_num(_value(signal, "trend_strength", 0.0)), -0.25, 0.25) / 0.25,
        "volatility": _clip(_num(_value(signal, "volatility_20d", 0.35)), 0.0, 1.5) / 1.5,
        "volume": _clip(_num(_value(signal, "volume_ratio", 1.0)), 0.0, 4.0) / 4.0,
        "news": _clip(_num(_value(signal, "news_sentiment", 0.0)), -1.0, 1.0),
        "relative_strength": _clip(_num(_value(signal, "relative_strength", 0.0)), -0.40, 0.40) / 0.40,
        "event_risk": _clip(_num(_value(signal, "event_risk_score", 20.0)), 0.0, 100.0) / 100.0,
    }


WEIGHTS = {
    "alpha": 1.5,
    "confidence": 1.1,
    "momentum_5d": 0.8,
    "momentum_20d": 1.2,
    "trend": 1.2,
    "volatility": 1.0,
    "volume": 0.6,
    "news": 0.7,
    "relative_strength": 1.0,
    "event_risk": 0.9,
}


def setup_similarity(current: dict[str, float], historical: dict[str, float]) -> float:
    weighted_distance = 0.0
    total_weight = 0.0
    for key, weight in WEIGHTS.items():
        weighted_distance += weight * (current.get(key, 0.0) - historical.get(key, 0.0)) ** 2
        total_weight += weight
    distance = math.sqrt(weighted_distance / max(total_weight, 1e-9))
    return _clip(1.0 - distance, 0.0, 1.0)


@dataclass(frozen=True)
class MarketMemoryAssessment:
    analog_count: int
    effective_sample_size: float
    win_rate: float | None
    average_return_pct: float | None
    median_return_pct: float | None
    average_similarity: float
    confidence: float
    score_adjustment: float
    probability_adjustment_pct: float
    pattern_label: str
    veto: bool
    summary: str
    analogs: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _record_vector(record: dict[str, Any]) -> dict[str, float]:
    payload = record.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    if isinstance(payload, dict) and isinstance(payload.get("features"), dict):
        return {k: _num(v) for k, v in payload["features"].items()}
    return {
        "alpha": _num(record.get("alpha_score")) / 100.0,
        "confidence": _num(record.get("probability_of_profit"), 50.0) / 100.0,
        "momentum_5d": _num(record.get("momentum_5d")),
        "momentum_20d": _num(record.get("momentum_20d")),
        "trend": _num(record.get("trend_strength")),
        "volatility": _num(record.get("volatility")),
        "volume": _num(record.get("volume_ratio"), 1.0) / 4.0,
        "news": _num(record.get("news_sentiment")),
        "relative_strength": _num(record.get("relative_strength")),
        "event_risk": _num(record.get("event_risk_score"), 20.0) / 100.0,
    }


def assess_market_memory(signal: Any, historical_records: Iterable[dict[str, Any]] | None = None) -> MarketMemoryAssessment:
    records = list(historical_records or [])
    if not records:
        return MarketMemoryAssessment(0, 0.0, None, None, None, 0.0, 0.0, 0.0, 0.0, "NO HISTORY", False, "No completed historical analogs are available yet.", [])

    current = feature_vector(signal)
    candidates: list[dict[str, Any]] = []
    for record in records:
        pnl = _num(record.get("pnl"), float("nan"))
        entry_value = abs(_num(record.get("entry_value"), 0.0))
        return_pct = record.get("return_pct")
        if return_pct is None:
            return_pct = pnl / entry_value if entry_value > 0 else _num(record.get("realized_return_pct"), float("nan"))
        return_pct = _num(return_pct, float("nan"))
        if not math.isfinite(return_pct):
            continue
        similarity = setup_similarity(current, _record_vector(record))
        if similarity < 0.45:
            continue
        candidates.append({
            "symbol": record.get("symbol"),
            "market": record.get("market"),
            "regime": record.get("market_regime"),
            "return_pct": return_pct,
            "pnl": pnl,
            "similarity": similarity,
            "exit_reason": record.get("exit_reason"),
            "created_at": record.get("created_at"),
        })

    candidates.sort(key=lambda x: x["similarity"], reverse=True)
    analogs = candidates[:25]
    if not analogs:
        return MarketMemoryAssessment(0, 0.0, None, None, None, 0.0, 0.0, 0.0, 0.0, "NO MATCH", False, "History exists, but no sufficiently similar completed setup was found.", [])

    weights = [max(0.01, x["similarity"] ** 3) for x in analogs]
    total_weight = sum(weights)
    weighted_returns = [x["return_pct"] * w for x, w in zip(analogs, weights)]
    avg_return = sum(weighted_returns) / total_weight
    wins = sum(w for x, w in zip(analogs, weights) if x["return_pct"] > 0)
    win_rate = wins / total_weight
    avg_similarity = sum(x["similarity"] * w for x, w in zip(analogs, weights)) / total_weight
    effective_n = total_weight ** 2 / max(sum(w * w for w in weights), 1e-9)
    sorted_returns = sorted(x["return_pct"] for x in analogs)
    median = sorted_returns[len(sorted_returns) // 2]
    sample_confidence = _clip(effective_n / max(MEMORY_MIN_ANALOGS * 2.0, 1.0), 0.0, 1.0)
    confidence = sample_confidence * avg_similarity

    edge = (win_rate - 0.5) * 2.0
    return_edge = _clip(avg_return / 0.08, -1.0, 1.0)
    raw_adjustment = MEMORY_MAX_ADJUSTMENT * confidence * (0.65 * edge + 0.35 * return_edge)
    adjustment = _clip(raw_adjustment, -MEMORY_MAX_ADJUSTMENT, MEMORY_MAX_ADJUSTMENT)
    probability_adjustment = _clip(adjustment * 0.55, -4.0, 4.0)
    veto = len(analogs) >= MEMORY_VETO_MIN_ANALOGS and confidence >= 0.55 and win_rate < MEMORY_VETO_WIN_RATE and avg_return < 0

    if adjustment >= 3.0:
        label = "HISTORICALLY STRONG"
    elif adjustment >= 0.75:
        label = "HISTORICALLY SUPPORTIVE"
    elif adjustment <= -3.0:
        label = "HISTORICALLY WEAK"
    elif adjustment <= -0.75:
        label = "HISTORICALLY CAUTIONARY"
    else:
        label = "MIXED HISTORY"

    summary = (
        f"{label}: {len(analogs)} similar completed setups, weighted win rate {win_rate:.0%}, "
        f"average return {avg_return:+.2%}, similarity {avg_similarity:.0%}, score adjustment {adjustment:+.1f}."
    )
    return MarketMemoryAssessment(
        len(analogs), round(effective_n, 2), round(win_rate, 4), round(avg_return, 6),
        round(median, 6), round(avg_similarity, 4), round(confidence, 4), round(adjustment, 2),
        round(probability_adjustment, 2), label, veto, summary, analogs[:8],
    )


def load_trade_memory(market: str, symbol: str | None = None, limit: int = MEMORY_LOOKBACK_LIMIT) -> list[dict[str, Any]]:
    """Load completed Trade DNA. Failure is neutral so workers never crash for missing history."""
    try:
        from database import connect
        with connect() as conn:
            if symbol:
                return list(conn.execute(
                    """SELECT *, CASE WHEN payload ? 'entry_value' THEN (payload->>'entry_value')::double precision ELSE NULL END AS entry_value,
                              CASE WHEN payload ? 'return_pct' THEN (payload->>'return_pct')::double precision ELSE NULL END AS return_pct
                       FROM trade_dna WHERE market=%s AND pnl IS NOT NULL
                       ORDER BY (symbol=%s) DESC, created_at DESC LIMIT %s""",
                    (market, symbol, limit),
                ).fetchall())
            return list(conn.execute(
                "SELECT * FROM trade_dna WHERE market=%s AND pnl IS NOT NULL ORDER BY created_at DESC LIMIT %s",
                (market, limit),
            ).fetchall())
    except Exception:
        return []


def record_decision_observation(market: str, signal: Any, decision: dict[str, Any]) -> None:
    try:
        from database import connect
        with connect() as conn:
            conn.execute(
                """INSERT INTO market_memory_observations
                   (market,symbol,regime,feature_vector,decision_payload,created_at)
                   VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,%s)""",
                (
                    market,
                    str(_value(signal, "symbol", "")),
                    str(_value(signal, "regime", "neutral")),
                    json.dumps(feature_vector(signal)),
                    json.dumps(decision),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
    except Exception:
        return


def record_closed_trade_memory(
    *, market: str, symbol: str, position: dict[str, Any], exit_price: float,
    pnl: float, exit_reason: str, quantity: float,
) -> None:
    """Persist a completed trade fingerprint for future analog matching."""
    try:
        from database import connect
        with connect() as conn:
            audit = conn.execute(
                """SELECT payload, opportunity_score, created_at FROM oracle_decision_audit
                   WHERE market=%s AND symbol=%s AND approved=TRUE
                   ORDER BY id DESC LIMIT 1""",
                (market, symbol),
            ).fetchone() or {}
            payload = audit.get("payload") or {}
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            quant = payload.get("quant", {}) if isinstance(payload, dict) else {}
            entry_price = _num(position.get("average_price") or position.get("entry_price"), exit_price)
            entry_value = abs(entry_price * quantity)
            return_pct = pnl / entry_value if entry_value > 0 else 0.0
            features = payload.get("features") if isinstance(payload, dict) else None
            if not isinstance(features, dict):
                features = {}
            dna_payload = {
                "entry_value": entry_value,
                "return_pct": return_pct,
                "features": features,
                "oracle_decision": payload,
            }
            conn.execute(
                """INSERT INTO trade_dna
                   (market,symbol,entry_time,exit_time,market_regime,alpha_score,execution_score,
                    risk_score,relative_value_score,trade_quality,expected_value_pct,estimated_cost_pct,
                    adverse_selection_score,probability_of_profit,risk_reward_ratio,entry_reason,exit_reason,
                    pnl,holding_minutes,payload,created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                (
                    market, symbol, position.get("opened_at"), datetime.now(timezone.utc).isoformat(),
                    payload.get("market_regime", "neutral") if isinstance(payload, dict) else "neutral",
                    quant.get("alpha_score"), quant.get("execution_score"), quant.get("risk_score"),
                    quant.get("relative_value_score"), payload.get("opportunity_score") if isinstance(payload, dict) else audit.get("opportunity_score"),
                    quant.get("net_expected_value_pct"), quant.get("estimated_cost_pct"),
                    quant.get("adverse_selection_score"), payload.get("probability_of_profit") if isinstance(payload, dict) else None,
                    payload.get("risk_reward_ratio") if isinstance(payload, dict) else None,
                    payload.get("reason") if isinstance(payload, dict) else None, exit_reason, pnl,
                    None, json.dumps(dna_payload), datetime.now(timezone.utc).isoformat(),
                ),
            )
    except Exception:
        return


def memory_dashboard_summary(limit: int = 500) -> dict[str, Any]:
    records = load_trade_memory("cash", limit=limit) + load_trade_memory("crypto", limit=limit)
    if not records:
        return {"completed_trades": 0, "win_rate": None, "average_return_pct": None, "best_patterns": [], "weak_patterns": []}
    returns = []
    groups: dict[str, list[float]] = {}
    for r in records:
        payload = r.get("payload") or {}
        if isinstance(payload, str):
            try: payload = json.loads(payload)
            except Exception: payload = {}
        ret = payload.get("return_pct") if isinstance(payload, dict) else None
        if ret is None:
            ret = _num(r.get("pnl")) / max(abs(_num(payload.get("entry_value"))) if isinstance(payload, dict) else 0.0, 1e-9)
        ret = _num(ret, float("nan"))
        if not math.isfinite(ret):
            continue
        returns.append(ret)
        regime = str(r.get("market_regime") or "neutral")
        groups.setdefault(regime, []).append(ret)
    pattern_rows = [{"pattern": k, "trades": len(v), "win_rate": sum(x > 0 for x in v)/len(v), "average_return_pct": sum(v)/len(v)} for k,v in groups.items()]
    pattern_rows.sort(key=lambda x: x["average_return_pct"], reverse=True)
    return {
        "completed_trades": len(returns),
        "win_rate": sum(x > 0 for x in returns) / len(returns) if returns else None,
        "average_return_pct": sum(returns) / len(returns) if returns else None,
        "best_patterns": pattern_rows[:5],
        "weak_patterns": list(reversed(pattern_rows[-5:])),
    }
