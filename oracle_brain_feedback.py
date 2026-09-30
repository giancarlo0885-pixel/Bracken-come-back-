from __future__ import annotations

"""Bounded feedback from durable Oracle Brain outcomes into paper ranking.

This module is deliberately soft influence only. Mature exact-provenance outcome
memory may adjust opportunity ranking, but it cannot create trade direction,
approve an order, change hard risk gates, enable broker submission, or arm live
trading.
"""

import math
import time
from typing import Any

from paper_strategy_economics import normalize_strategy_identity, strategy_identity


_CACHE: dict[tuple[str, str, str, str], tuple[float, dict[str, Any]]] = {}
_CACHE_SECONDS = 60.0


def _value(obj: Any, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _normalized_market(value: Any) -> str:
    market = str(value or "").strip().lower()
    return "cash" if market in {"stock", "stocks", "equity", "equities", "cash"} else market


def _normalized_regime(value: Any) -> str:
    return str(value or "unknown").strip().lower() or "unknown"


def _json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            import json
            decoded = json.loads(value)
        except Exception:
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    return {}


_GREEN_FEATURES = (
    "momentum_5d", "momentum_20d", "trend_strength", "rsi_14", "volume_ratio",
    "news_sentiment", "macd_hist", "atr_pct", "bollinger_position",
    "volatility_20d", "dip_depth_pct", "rebound_from_low_pct",
)


def green_core_profile(rows: list[dict[str, Any]], signal: Any, *, min_samples: int = 30) -> dict[str, Any]:
    """Learn bounded green-vs-red feature separation from exact completed episodes.

    Only features present in both positive and negative cohorts can influence ranking.
    This avoids treating a missing feature or a single winner as a profitable rule.
    """
    complete = []
    for row in rows:
        snapshot = _json_obj(row.get("feature_snapshot"))
        pnl = _number(row.get("net_pnl"), float("nan"))
        if not math.isfinite(pnl) or pnl == 0:
            continue
        complete.append((snapshot, pnl > 0))
    positives = [snap for snap, win in complete if win]
    negatives = [snap for snap, win in complete if not win]
    if len(complete) < min_samples or len(positives) < max(5, min_samples // 5) or len(negatives) < max(5, min_samples // 5):
        return {"status": "insufficient", "samples": len(complete), "green_samples": len(positives), "red_samples": len(negatives), "adjustment": 0.0, "features": []}

    learned = []
    weighted_match = 0.0
    total_weight = 0.0
    for name in _GREEN_FEATURES:
        pos = [_number(s.get(name), float("nan")) for s in positives if s.get(name) is not None]
        neg = [_number(s.get(name), float("nan")) for s in negatives if s.get(name) is not None]
        pos = [v for v in pos if math.isfinite(v)]
        neg = [v for v in neg if math.isfinite(v)]
        if len(pos) < 5 or len(neg) < 5:
            continue
        pos_mean = sum(pos) / len(pos)
        neg_mean = sum(neg) / len(neg)
        combined = pos + neg
        mean = sum(combined) / len(combined)
        variance = sum((v - mean) ** 2 for v in combined) / max(1, len(combined) - 1)
        scale = math.sqrt(variance)
        if scale <= 1e-12:
            continue
        separation = (pos_mean - neg_mean) / scale
        if abs(separation) < 0.20:
            continue
        current = _number(_value(signal, name, float("nan")), float("nan"))
        if not math.isfinite(current):
            continue
        midpoint = (pos_mean + neg_mean) / 2.0
        direction = 1.0 if separation > 0 else -1.0
        matches_green = (current - midpoint) * direction >= 0
        weight = min(1.0, abs(separation))
        weighted_match += weight * (1.0 if matches_green else -1.0)
        total_weight += weight
        learned.append({
            "feature": name,
            "green_mean": round(pos_mean, 6),
            "red_mean": round(neg_mean, 6),
            "standardized_separation": round(separation, 4),
            "current": round(current, 6),
            "matches_green": matches_green,
        })
    if not learned or total_weight <= 0:
        return {"status": "no_separation", "samples": len(complete), "green_samples": len(positives), "red_samples": len(negatives), "adjustment": 0.0, "features": []}
    depth = min(1.0, len(complete) / float(max(min_samples * 3, 1)))
    score = weighted_match / total_weight
    adjustment = max(-2.0, min(2.0, score * (0.75 + 1.25 * depth)))
    return {
        "status": "ok", "samples": len(complete), "green_samples": len(positives),
        "red_samples": len(negatives), "adjustment": round(adjustment, 3),
        "features": learned[:8],
    }


def _summarize_rows(rows: list[dict[str, Any]], *, min_samples: int) -> dict[str, Any]:
    samples = len(rows)
    returns = [
        _number(row.get("return_pct"), float("nan"))
        for row in rows
        if row.get("return_pct") is not None
    ]
    returns = [value for value in returns if math.isfinite(value)]
    pnls = [_number(row.get("net_pnl")) for row in rows]
    gross_win = sum(value for value in pnls if value > 0)
    gross_loss = abs(sum(value for value in pnls if value < 0))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
    expectancy_pct = (sum(returns) / len(returns)) if returns else None
    win_rate = (sum(1 for value in pnls if value > 0) / samples) if samples else 0.0
    quality_values = [
        max(0.0, min(1.0, _number(row.get("confidence"), 0.0)))
        * max(0.0, min(1.0, _number(row.get("freshness_score"), 0.0)))
        for row in rows
    ]
    evidence_quality = (sum(quality_values) / len(quality_values)) if quality_values else 0.0

    polarity = "insufficient"
    adjustment = 0.0
    if samples >= min_samples and expectancy_pct is not None:
        depth = min(1.0, samples / float(max(min_samples * 3, 1)))
        if expectancy_pct > 0.0 and profit_factor >= 1.10:
            polarity = "positive"
            adjustment = min(3.0, 0.75 + 2.25 * depth * evidence_quality)
        elif expectancy_pct < 0.0 and profit_factor <= 1.0:
            polarity = "negative"
            adjustment = -min(4.0, 1.0 + 3.0 * depth * evidence_quality)
        else:
            polarity = "mixed"

    return {
        "samples": samples,
        "expectancy_pct": None if expectancy_pct is None else round(expectancy_pct, 6),
        "net_pnl": round(sum(pnls), 8),
        "win_rate": round(win_rate, 6),
        "profit_factor": round(profit_factor, 6),
        "evidence_quality": round(evidence_quality, 6),
        "polarity": polarity,
        "ranking_adjustment": round(adjustment, 3),
    }


def summarize_outcome_memory(
    rows: list[dict[str, Any]],
    *,
    strategy: str,
    regime: str,
    symbol: str,
    min_samples: int = 30,
) -> dict[str, Any]:
    """Select the narrowest mature cohort; immature memory has zero influence."""
    target_strategy = normalize_strategy_identity(strategy)
    target_regime = _normalized_regime(regime)
    target_symbol = str(symbol or "").upper().strip()

    normalized: list[dict[str, Any]] = []
    for raw in rows:
        item = dict(raw)
        item["strategy"] = normalize_strategy_identity(item.get("strategy"))
        item["regime"] = _normalized_regime(item.get("regime"))
        item["symbol"] = str(item.get("symbol") or "").upper().strip()
        normalized.append(item)

    scopes = [
        (
            "symbol_strategy_regime",
            [
                row for row in normalized
                if row["strategy"] == target_strategy
                and row["regime"] == target_regime
                and row["symbol"] == target_symbol
            ],
        ),
        (
            "strategy_regime",
            [
                row for row in normalized
                if row["strategy"] == target_strategy and row["regime"] == target_regime
            ],
        ),
        (
            "strategy",
            [row for row in normalized if row["strategy"] == target_strategy],
        ),
    ]
    chosen_scope, chosen_rows = scopes[-1]
    for scope, cohort in scopes:
        if len(cohort) >= min_samples:
            chosen_scope, chosen_rows = scope, cohort
            break
    else:
        chosen_scope, chosen_rows = max(scopes, key=lambda item: len(item[1]))

    summary = _summarize_rows(chosen_rows, min_samples=min_samples)
    return {
        **summary,
        "scope": chosen_scope,
        "strategy": target_strategy,
        "regime": target_regime,
        "symbol": target_symbol,
        "mature": bool(summary["samples"] >= min_samples and summary["polarity"] != "insufficient"),
        "execution_impact": "BOUNDED_RANKING_ONLY" if summary["ranking_adjustment"] else "NONE",
    }


def outcome_memory_for_signal(
    signal: Any,
    *,
    market: str,
    symbol: str | None = None,
    min_samples: int = 30,
    max_rows: int = 0,
) -> dict[str, Any]:
    """Return bounded Brain outcome feedback for the current paper opportunity."""
    normalized_market = _normalized_market(market)
    target_symbol = str(symbol or _value(signal, "symbol", "") or "").upper().strip()
    target_strategy = strategy_identity(signal)
    target_regime = _normalized_regime(_value(signal, "regime", "unknown"))

    try:
        from oracle_brain import runtime_safety_state

        safety = runtime_safety_state()
    except Exception:
        safety = {"safe_research_boundary": False}
    if not safety.get("safe_research_boundary"):
        return {
            "status": "inactive",
            "reason": "brain outcome feedback is paper-only and live-disarmed",
            "market": normalized_market,
            "symbol": target_symbol,
            "strategy": target_strategy,
            "regime": target_regime,
            "ranking_adjustment": 0.0,
            "execution_impact": "NONE",
        }

    key = (normalized_market, target_symbol, normalize_strategy_identity(target_strategy), target_regime)
    cached = _CACHE.get(key)
    now = time.monotonic()
    if cached and now - cached[0] <= _CACHE_SECONDS:
        return dict(cached[1])

    try:
        from database import rows as fetch_rows

        limit = max(0, int(max_rows))
        if limit:
            sql = """
                SELECT market,symbol,strategy,regime,net_pnl,return_pct,
                       confidence,freshness_score,exit_time,feature_snapshot
                FROM oracle_brain_episodes
                WHERE market=%s AND provenance_status='exact'
                ORDER BY exit_time DESC
                LIMIT %s
            """
            params = (normalized_market, limit)
        else:
            # Learning defaults to the full exact-provenance history retained in
            # durable Brain memory. A caller may still bound this for diagnostics.
            sql = """
                SELECT market,symbol,strategy,regime,net_pnl,return_pct,
                       confidence,freshness_score,exit_time,feature_snapshot
                FROM oracle_brain_episodes
                WHERE market=%s AND provenance_status='exact'
                ORDER BY exit_time DESC
            """
            params = (normalized_market,)
        records = [dict(row) for row in fetch_rows(sql, params)]
    except Exception as exc:
        return {
            "status": "unavailable",
            "reason": exc.__class__.__name__,
            "market": normalized_market,
            "symbol": target_symbol,
            "strategy": target_strategy,
            "regime": target_regime,
            "ranking_adjustment": 0.0,
            "execution_impact": "NONE",
        }

    result = summarize_outcome_memory(
        records,
        strategy=target_strategy,
        regime=target_regime,
        symbol=target_symbol,
        min_samples=max(5, int(min_samples)),
    )
    target_strategy_norm = normalize_strategy_identity(target_strategy)
    cohort_rows = [
        row for row in records
        if normalize_strategy_identity(row.get("strategy")) == target_strategy_norm
        and _normalized_regime(row.get("regime")) == target_regime
    ]
    symbol_rows = [row for row in cohort_rows if str(row.get("symbol") or "").upper().strip() == target_symbol]
    green_rows = symbol_rows if len(symbol_rows) >= max(5, int(min_samples)) else cohort_rows
    green_core = green_core_profile(green_rows, signal, min_samples=max(5, int(min_samples)))
    base_adjustment = _number(result.get("ranking_adjustment"))
    green_adjustment = _number(green_core.get("adjustment"))
    result["base_ranking_adjustment"] = round(base_adjustment, 3)
    result["green_core"] = green_core
    result["ranking_adjustment"] = round(max(-4.0, min(3.0, base_adjustment + green_adjustment)), 3)
    result.update({
        "status": "ok",
        "market": normalized_market,
        "reason": (
            f"mature_{result['polarity']}_exact_outcomes"
            if result["ranking_adjustment"]
            else "insufficient_or_mixed_exact_outcomes"
        ),
    })
    _CACHE[key] = (now, dict(result))
    return result


__all__ = [
    "green_core_profile",
    "outcome_memory_for_signal",
    "summarize_outcome_memory",
]
