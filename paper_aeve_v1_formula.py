from __future__ import annotations

"""AEVE V1 paper-only scoring primitives.

This module contains no order submission, sizing, cooldown, or broker code.  It is
intended for forward shadow evaluation before any promotion decision.
"""

import math
from dataclasses import dataclass


def _f(value: float, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


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
    *,
    expected_net_edge_pct: float,
    mfe_pct: float,
    mae_pct: float,
    round_trip_cost_pct: float,
    loss_streak: int,
    price_above_recent_low_pct: float,
    rebound_from_low_pct: float,
    rsi: float | None,
    trend_confirmed: bool,
    regime_expectancy_positive: bool,
    profit_factor: float,
    min_samples: int,
) -> AEVEEntry:
    """Score a candidate using information available at entry time only.

    The intent is to buy near a locally depressed price *after* rebound evidence,
    rather than blindly buying weakness, while requiring historical net expectancy
    to cover execution friction.  Thresholds are research defaults and must be
    walk-forward calibrated; they are not live execution rules.
    """
    ev = _f(expected_net_edge_pct)
    mfe = max(0.0, _f(mfe_pct))
    mae = abs(min(0.0, _f(mae_pct)))
    cost = max(0.0, _f(round_trip_cost_pct))
    above_low = max(0.0, _f(price_above_recent_low_pct))
    rebound = max(0.0, _f(rebound_from_low_pct))
    pf = max(0.0, _f(profit_factor))
    samples = max(0, int(min_samples))
    streak = min(max(0, int(loss_streak)), 8)

    # Near the rolling low is useful only if price has begun to rebound.  Buying
    # a continuing collapse receives little/no credit.
    dip_quality = _clip(1.0 - above_low / 2.0, 0.0, 1.0)
    rebound_quality = _clip(rebound / max(cost * 2.0, 0.20), 0.0, 1.0)

    # Reward cohorts whose favorable excursion dominates adverse excursion.
    excursion_quality = _clip((mfe - mae) / max(mfe + mae, 0.10), -1.0, 1.0)

    # A candidate should have room to move materially farther than its expected
    # round-trip friction.  2x is deliberately conservative for paper research.
    cost_coverage = _clip((mfe - 2.0 * cost) / max(mfe, 0.10), -1.0, 1.0)

    rsi_quality = 0.0
    if rsi is not None:
        r = _clip(_f(rsi, 50.0), 0.0, 100.0)
        # Prefer recovery from a depressed/neutral zone; avoid rewarding extreme
        # overbought entries or treating an oversold reading alone as a buy.
        if 32.0 <= r <= 55.0:
            rsi_quality = 1.0 - abs(r - 43.5) / 11.5
        elif r > 70.0:
            rsi_quality = -1.0

    streak_penalty = 0.06 * streak
    regime_penalty = 0.0 if regime_expectancy_positive else 0.30

    score = (
        1.60 * ev
        + 0.22 * dip_quality
        + 0.28 * rebound_quality
        + 0.35 * excursion_quality
        + 0.30 * cost_coverage
        + 0.12 * rsi_quality
        + (0.12 if trend_confirmed else 0.0)
        + 0.15 * _clip(pf - 1.0, -1.0, 1.0)
        - streak_penalty
        - regime_penalty
    )

    # Hard economic gates prevent a high technical score from overriding a
    # negative post-cost cohort.  These are shadow-study gates only.
    enough_history = samples >= 30
    economically_positive = ev > max(0.05, 0.35 * cost) and pf > 1.0
    excursion_positive = mfe > mae and mfe > 2.0 * cost
    rebound_confirmed = rebound_quality >= 0.50
    would_trade = bool(
        enough_history
        and regime_expectancy_positive
        and economically_positive
        and excursion_positive
        and rebound_confirmed
        and score > 0.25
    )

    return AEVEEntry(
        score=score,
        expected_net_edge_pct=ev,
        dip_quality=dip_quality,
        rebound_quality=rebound_quality,
        excursion_quality=excursion_quality,
        cost_coverage=cost_coverage,
        would_trade=would_trade,
    )


def should_take_profit(
    *,
    unrealized_return_pct: float,
    round_trip_cost_pct: float,
    mfe_since_entry_pct: float,
    pullback_from_peak_pct: float,
    minimum_net_profit_pct: float = 0.20,
) -> bool:
    """Paper research exit: protect a favorable move after costs.

    This does not attempt to identify the unknowable exact top.  It waits for a
    meaningful net gain and then allows a trailing giveback from observed MFE.
    """
    ret = _f(unrealized_return_pct)
    cost = max(0.0, _f(round_trip_cost_pct))
    mfe = max(0.0, _f(mfe_since_entry_pct))
    pullback = max(0.0, _f(pullback_from_peak_pct))
    net = ret - cost
    required_net = max(_f(minimum_net_profit_pct, 0.20), cost)
    trailing_trigger = max(0.15, 0.30 * mfe)
    return bool(net >= required_net and pullback >= trailing_trigger)
