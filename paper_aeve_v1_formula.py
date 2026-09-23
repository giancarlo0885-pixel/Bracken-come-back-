from __future__ import annotations

"""AEVE V1 paper-only scoring primitives.

This module contains no order submission, sizing, cooldown, or broker code. It is
intended for forward shadow evaluation before any promotion decision.
"""

import math
from dataclasses import dataclass
from typing import Any, Mapping


def _f(value: float, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass(frozen=True)
class AEVEScoringConfig:
    min_edge_pct: float = 0.05
    min_profit_factor: float = 1.00
    min_mfe_mae_ratio: float = 1.00
    min_mfe_cost_multiple: float = 2.00
    max_loss_streak: int = 8
    score_gate: float = 0.25
    rebound_gate: float = 0.50
    require_positive_regime: bool = True


def _config(config: AEVEScoringConfig | Mapping[str, Any] | None) -> AEVEScoringConfig:
    if config is None:
        return AEVEScoringConfig()
    if isinstance(config, AEVEScoringConfig):
        return config
    allowed = AEVEScoringConfig.__dataclass_fields__
    return AEVEScoringConfig(**{k: v for k, v in dict(config).items() if k in allowed})


@dataclass(frozen=True)
class AEVEEntry:
    score: float
    expected_net_edge_pct: float
    dip_quality: float
    rebound_quality: float
    excursion_quality: float
    cost_coverage: float
    would_trade: bool


def score_entry(
    *, expected_net_edge_pct: float, mfe_pct: float, mae_pct: float,
    round_trip_cost_pct: float, loss_streak: int, dip_depth_pct: float,
    rebound_from_low_pct: float, rsi: float | None, trend_confirmed: bool,
    regime_expectancy_positive: bool, profit_factor: float, min_samples: int,
    config: AEVEScoringConfig | Mapping[str, Any] | None = None,
) -> AEVEEntry:
    """Score a forward paper candidate using the frozen generation configuration."""
    cfg = _config(config)
    ev = _f(expected_net_edge_pct)
    mfe = max(0.0, _f(mfe_pct))
    mae = abs(min(0.0, _f(mae_pct)))
    cost = max(0.0, _f(round_trip_cost_pct))
    dip_depth = max(0.0, _f(dip_depth_pct))
    rebound = max(0.0, _f(rebound_from_low_pct))
    pf = max(0.0, _f(profit_factor))
    samples = max(0, int(min_samples))
    streak = max(0, int(loss_streak))

    # Dip depth is high-to-low drawdown in percentage points. Preserve the
    # existing shallow-dip preference and scale; recovery is scored separately.
    dip_quality = _clip(1.0 - dip_depth / 2.0, 0.0, 1.0)
    rebound_quality = _clip(rebound / max(cost * 2.0, 0.20), 0.0, 1.0)
    excursion_quality = _clip((mfe - mae) / max(mfe + mae, 0.10), -1.0, 1.0)
    cost_coverage = _clip((mfe - cfg.min_mfe_cost_multiple * cost) / max(mfe, 0.10), -1.0, 1.0)

    rsi_quality = 0.0
    if rsi is not None:
        r = _clip(_f(rsi, 50.0), 0.0, 100.0)
        if 32.0 <= r <= 55.0:
            rsi_quality = 1.0 - abs(r - 43.5) / 11.5
        elif r > 70.0:
            rsi_quality = -1.0

    # Do not cap the observed streak before the hard gate: a configured maximum
    # must be capable of rejecting a candidate after too many consecutive losses.
    streak_penalty = 0.06 * min(streak, max(0, cfg.max_loss_streak))
    regime_penalty = 0.0 if regime_expectancy_positive else 0.30
    score = (
        1.60 * ev + 0.22 * dip_quality + 0.28 * rebound_quality
        + 0.35 * excursion_quality + 0.30 * cost_coverage + 0.12 * rsi_quality
        + (0.12 if trend_confirmed else 0.0)
        + 0.15 * _clip(pf - 1.0, -1.0, 1.0) - streak_penalty - regime_penalty
    )

    enough_history = samples >= 30
    regime_ok = regime_expectancy_positive or not cfg.require_positive_regime
    economically_positive = ev > max(cfg.min_edge_pct, 0.35 * cost) and pf > cfg.min_profit_factor
    excursion_positive = (
        mfe > cfg.min_mfe_mae_ratio * mae
        and mfe > cfg.min_mfe_cost_multiple * cost
    )
    rebound_confirmed = rebound_quality >= cfg.rebound_gate
    loss_streak_ok = streak <= cfg.max_loss_streak
    would_trade = bool(
        enough_history and regime_ok and economically_positive and excursion_positive
        and rebound_confirmed and loss_streak_ok and score > cfg.score_gate
    )
    return AEVEEntry(score, ev, dip_quality, rebound_quality, excursion_quality, cost_coverage, would_trade)


def should_take_profit(*, unrealized_return_pct: float, round_trip_cost_pct: float,
                       mfe_since_entry_pct: float, pullback_from_peak_pct: float,
                       minimum_net_profit_pct: float = 0.20) -> bool:
    """Paper research exit: protect a favorable move after costs."""
    ret = _f(unrealized_return_pct)
    cost = max(0.0, _f(round_trip_cost_pct))
    mfe = max(0.0, _f(mfe_since_entry_pct))
    pullback = max(0.0, _f(pullback_from_peak_pct))
    net = ret - cost
    required_net = max(_f(minimum_net_profit_pct, 0.20), cost)
    trailing_trigger = max(0.15, 0.30 * mfe)
    return bool(net >= required_net and pullback >= trailing_trigger)
