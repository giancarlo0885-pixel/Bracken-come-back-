from __future__ import annotations

"""Bounded feedback from durable Oracle Brain outcomes into paper ranking.

This module is deliberately soft influence only. Mature exact-provenance outcome
memory may adjust opportunity ranking, but it cannot create trade direction,
approve an order, change hard risk gates, enable broker submission, or arm live
trading.
"""

import math
from typing import Any

from paper_strategy_economics import normalize_strategy_identity, strategy_identity


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
    "volatility_20d", "dip_depth_pct", "rebound_from_low_pct", "rebound_pct",
    "dip_rebound_score", "schwager_trend_score", "schwager_breakout_score",
    "schwager_oscillator_score", "schwager_setup_score",
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
        "features": learned,
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


def _counterfactual_memory_for_signal(signal: Any, *, market: str, symbol: str, min_samples: int, max_rows: int = 0) -> dict[str, Any]:
    """Learn from rejected decisions only when simulated round-trip economics are known."""
    try:
        from database import rows as fetch_rows
        from paper_execution_reality import simulate_fill
        limit = max(0, int(max_rows))
        select = """SELECT c.outcome_class,c.return_pct,c.entry_price,c.horizon_price,d.payload
                    FROM oracle_counterfactual_outcomes c
                    JOIN oracle_decision_audit d ON d.id=c.decision_id
                    WHERE c.market=%s AND c.symbol=%s
                    ORDER BY c.decision_time DESC"""
        if limit:
            select += " LIMIT %s"
            params = (market, symbol, limit)
        else:
            params = (market, symbol)
        records = [dict(row) for row in fetch_rows(select, params)]
    except Exception as exc:
        return {"status": "unavailable", "samples": 0, "adjustment": 0.0, "reason": exc.__class__.__name__}

    profitable: list[dict[str, Any]] = []
    avoided: list[dict[str, Any]] = []
    unevaluable = 0
    for record in records:
        outcome = str(record.get("outcome_class") or "")
        if outcome == "avoided_loss":
            avoided.append(record)
            continue
        if outcome != "missed_winner":
            continue
        try:
            entry = float(record.get("entry_price"))
            horizon = float(record.get("horizon_price"))
            if not math.isfinite(entry) or not math.isfinite(horizon) or entry <= 0 or horizon <= 0:
                raise ValueError("invalid counterfactual price")
            payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
            order_value = None
            for key in ("order_value", "notional", "notional_usd", "position_value", "planned_notional"):
                try:
                    candidate = float(payload.get(key)) if payload.get(key) not in (None, "") else None
                except (TypeError, ValueError):
                    candidate = None
                if candidate is not None and math.isfinite(candidate) and candidate > 0:
                    order_value = candidate
                    break
            buy = simulate_fill(side="BUY", market=market, reference_price=entry, order_value=order_value)
            sell = simulate_fill(side="SELL", market=market, reference_price=horizon, order_value=order_value)
            net_return_pct = ((sell.fill_price / buy.fill_price) - 1.0) * 100.0
            record["simulated_post_cost_return_pct"] = net_return_pct
            if net_return_pct > 0:
                profitable.append(record)
        except (TypeError, ValueError, OverflowError):
            unevaluable += 1

    informative = len(profitable) + len(avoided)
    required = max(5, int(min_samples))
    if informative < required:
        return {
            "status": "insufficient", "samples": len(records), "informative": informative,
            "simulated_post_cost_profitable_missed": len(profitable), "avoided_losses": len(avoided),
            "unevaluable": unevaluable, "adjustment": 0.0,
            "economics": "SIMULATED_POST_COST",
        }
    balance = (len(profitable) - len(avoided)) / float(informative)
    depth = min(1.0, informative / float(max(1, required * 3)))
    adjustment = max(-0.75, min(0.75, balance * depth * 0.75))
    return {
        "status": "ok", "samples": len(records), "informative": informative,
        "simulated_post_cost_profitable_missed": len(profitable), "avoided_losses": len(avoided),
        "unevaluable": unevaluable, "adjustment": round(adjustment, 3),
        "economics": "SIMULATED_POST_COST", "execution_impact": "BOUNDED_RANKING_ONLY",
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

    # Feature-dependent ranking must be recomputed for each signal, even within
    # the same symbol/strategy/regime cache interval.

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
    # The winner formula consumes independently reconciled complete BUY lots,
    # rather than trusting SELL snapshots or counting split exits as samples.
    green_rows = []
    from paper_winner_memory import current_entry_features
    green_signal = current_entry_features(signal)
    try:
        from paper_exit_research import REPORT_VERSION, timestamp
        reports = fetch_rows("SELECT report FROM paper_exit_research_reports WHERE market=%s AND version=%s",
                             (normalized_market, REPORT_VERSION))
        cutoff = timestamp(_value(signal, "decision_timestamp"))
        if cutoff is None:
            from datetime import datetime, timezone
            cutoff = datetime.now(timezone.utc)
        memory = _json_obj(reports[0].get("report")) if reports else {}
        candidates = memory.get("winner_entry_memory", {}).get("records", [])
        from paper_regime_economics_shadow import classify_regime
        green_regime = target_regime if "__" in target_regime else classify_regime(
            feature_snapshot=green_signal)
        green_rows = [r for r in candidates
                      if normalize_strategy_identity(r.get("strategy")) == target_strategy_norm
                      and _normalized_regime(r.get("regime")) == green_regime
                      and timestamp(r.get("exit_time")) is not None
                      and timestamp(r["exit_time"]) < cutoff]
        matching = [r for r in green_rows if r.get("symbol") == target_symbol]
        if len(matching) >= max(5, int(min_samples)):
            green_rows = matching
    except Exception:
        green_rows = []
    green_core = green_core_profile(green_rows, green_signal, min_samples=max(5, int(min_samples)))
    green_core["provenance"] = "canonical-winner-entry-v1"
    green_core["input_units"] = "canonical_entry_fingerprint"
    base_adjustment = _number(result.get("ranking_adjustment"))
    green_adjustment = _number(green_core.get("adjustment"))
    counterfactual_memory = _counterfactual_memory_for_signal(
        signal, market=normalized_market, symbol=target_symbol, min_samples=max(5, int(min_samples)), max_rows=max_rows
    )
    counterfactual_adjustment = _number(counterfactual_memory.get("adjustment"))
    result["base_ranking_adjustment"] = round(base_adjustment, 3)
    result["green_core"] = green_core
    result["counterfactual_memory"] = counterfactual_memory
    result["ranking_adjustment"] = round(
        max(-4.0, min(3.0, base_adjustment + green_adjustment + counterfactual_adjustment)), 3
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
    return result


__all__ = [
    "green_core_profile",
    "outcome_memory_for_signal",
    "summarize_outcome_memory",
]
