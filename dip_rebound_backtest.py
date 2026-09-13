"""Chronological replay of the forward experiment with explicit modeled costs."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from entry_patterns import MIN_HISTORY, STRATEGY_VERSION, extract_entry_pattern, finite
from paper_dip_rebound import CONTROL_POLICY, POLICIES, TARGET_POLICY, modeled_fill, plan_entry


def _quote(price: float, timestamp: datetime, spread_bps: float) -> dict[str, Any]:
    spread = spread_bps / 10000
    return {"price": price, "bid": price * (1 - spread / 2), "ask": price * (1 + spread / 2),
            "quote_timestamp": timestamp.isoformat(), "evidence_kind": "historical_bar_with_modeled_spread"}


def bar_exit(state: dict[str, Any], bar: pd.Series, timestamp: datetime, policy: str) -> tuple[str | None, float | None, dict[str, Any]]:
    """Assume the adverse stop wins when a candle touches both stop and target.

    A trailing level uses the peak observed before this bar, never its unseen
    intrabar high. New peaks become eligible on the following bar.
    """
    updated = dict(state)
    stop, reason = state["stop"], "invalidation_stop"
    if policy == TARGET_POLICY and state["peak"] >= state["entry"] + state["risk"]:
        trailing = state["peak"] - state["risk"]
        if trailing > stop:
            stop, reason = trailing, "profit_trail"
    if float(bar.Low) <= stop:
        return reason, min(float(bar.Open), stop), updated
    if policy == TARGET_POLICY and float(bar.High) >= state["target"]:
        return "rebound_target", max(float(bar.Open), state["target"]), updated
    updated["peak"] = max(state["peak"], float(bar.High))
    if pd.Timestamp(timestamp) >= pd.Timestamp(state["deadline"]):
        return "time_exit", float(bar.Close), updated
    return None, None, updated


def replay(history: pd.DataFrame, *, spread_bps: float = 18.0, start_position: int = MIN_HISTORY) -> dict[str, Any]:
    """Decide on completed bars, enter at next open, report unresolved positions."""
    if finite(spread_bps) is None or not 0 <= spread_bps < 10000:
        raise ValueError("spread_bps must be nonnegative")
    if not isinstance(history.index, pd.DatetimeIndex) or history.index.tz is None:
        raise ValueError("timezone-aware historical bars required")
    columns = ["Open", "High", "Low", "Close", "Volume"]
    if (any(key not in history for key in columns) or not history.index.is_monotonic_increasing
            or history.index.has_duplicates or not np.isfinite(history[columns].to_numpy(dtype=float)).all()):
        raise ValueError("ordered finite historical OHLCV required")
    if ((history[columns[:4]] <= 0).any().any() or (history.Volume < 0).any()
            or (history.High < history[["Open", "Close", "Low"]].max(axis=1)).any()
            or (history.Low > history[["Open", "Close", "High"]].min(axis=1)).any()):
        raise ValueError("valid historical candle ranges required")
    rows: list[dict[str, Any]] = []
    pending: dict[str, dict[str, Any]] = {}
    decisions = 0
    rejected: dict[str, int] = {}
    for i in range(max(MIN_HISTORY, start_position), len(history)):
        timestamp = history.index[i].to_pydatetime()
        bar = history.iloc[i]
        # Freshness/continuity and all features are judged using past bars only.
        pattern = extract_entry_pattern(history.iloc[:i], asof=timestamp)
        if not pending:
            plan, reason = plan_entry(pattern, _quote(float(bar.Open), timestamp, spread_bps), timestamp)
            decisions += 1
            if plan:
                pending = {policy: dict(plan, entry_at=timestamp.isoformat(), entry_bar=pattern["bar_end"]) for policy in POLICIES}
            else:
                rejected[reason] = rejected.get(reason, 0) + 1
        for policy in list(pending):
            state = pending[policy]
            interval = pd.Timedelta(minutes=state["pattern"]["bar_minutes"])
            bar_end = (history.index[i] + interval).to_pydatetime()
            reason, reference, updated = bar_exit(state, bar, bar_end, policy)
            if reason:
                fill = modeled_fill("SELL", _quote(float(reference), bar_end, spread_bps))
                rows.append({"policy": policy, "entry_at": state["entry_at"], "entry_bar": state["entry_bar"],
                             "exit_bar_end": bar_end.isoformat(), "exit_reason": reason,
                             "entry_fill": state["entry"], "exit_fill": fill["fill_price"],
                             "net_return_pct": (fill["fill_price"] / state["entry"] - 1) * 100,
                             "context_regime": state["pattern"]["context_regime"]})
                del pending[policy]
            else:
                pending[policy] = updated
    summaries = {}
    for policy in POLICIES:
        values = [row["net_return_pct"] for row in rows if row["policy"] == policy]
        positive, negative = sum(max(0, x) for x in values), -sum(min(0, x) for x in values)
        summaries[policy] = {"closed": len(values), "win_rate": sum(x > 0 for x in values) / len(values) if values else None,
                             "net_expectancy_pct": sum(values) / len(values) if values else None,
                             "profit_factor": positive / negative if negative > 0 else None}
    pairs: dict[str, dict[str, float]] = {}
    for row in rows:
        pairs.setdefault(row["entry_bar"], {})[row["policy"]] = row["net_return_pct"]
    paired = [pair[TARGET_POLICY] - pair[CONTROL_POLICY] for pair in pairs.values() if len(pair) == 2]
    return {"strategy": STRATEGY_VERSION, "evidence_kind": "historical_replay_modeled_costs",
            "spread_bps": spread_bps, "decisions": decisions, "policies": summaries,
            "paired_closes": len(paired), "mean_exit_advantage_pct": sum(paired) / len(paired) if paired else None,
            "unresolved_trials": len(pending), "rejections": rejected, "trades": rows,
            "promotion": "NONE"}
