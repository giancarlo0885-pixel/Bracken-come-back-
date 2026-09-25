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
    max_rows: int = 3000,
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

        records = [
            dict(row)
            for row in fetch_rows(
                """
                SELECT market,symbol,strategy,regime,net_pnl,return_pct,
                       confidence,freshness_score,exit_time
                FROM oracle_brain_episodes
                WHERE market=%s AND provenance_status='exact'
                ORDER BY exit_time DESC
                LIMIT %s
                """,
                (normalized_market, max(100, min(10000, int(max_rows)))),
            )
        ]
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
    "outcome_memory_for_signal",
    "summarize_outcome_memory",
]
