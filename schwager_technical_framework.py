from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import numpy as np
import pandas as pd

from technical_indicators import rsi, macd, atr


@dataclass(frozen=True)
class SchwagerTechnicalAssessment:
    available: bool
    trend_state: str
    trend_score: float
    support: float | None
    resistance: float | None
    support_distance_pct: float | None
    resistance_distance_pct: float | None
    breakout_state: str
    breakout_score: float
    failed_breakout: bool
    oscillator_state: str
    oscillator_score: float
    setup_score: float
    pattern_tag: str
    suggested_stop: float | None
    objective_1: float | None
    reward_risk_ratio: float | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(dtype=float)
    value = frame[column]
    if isinstance(value, pd.DataFrame):
        value = value.iloc[:, -1]
    return pd.to_numeric(value, errors="coerce").dropna()


def _clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _empty(reason: str) -> SchwagerTechnicalAssessment:
    return SchwagerTechnicalAssessment(
        available=False,
        trend_state="unknown",
        trend_score=0.0,
        support=None,
        resistance=None,
        support_distance_pct=None,
        resistance_distance_pct=None,
        breakout_state="none",
        breakout_score=0.0,
        failed_breakout=False,
        oscillator_state="neutral",
        oscillator_score=0.0,
        setup_score=0.0,
        pattern_tag="",
        suggested_stop=None,
        objective_1=None,
        reward_risk_ratio=None,
        reason=reason,
    )


def assess_schwager_technical_structure(
    history: pd.DataFrame,
    *,
    support_window: int = 20,
    trend_fast: int = 10,
    trend_slow: int = 30,
) -> SchwagerTechnicalAssessment:
    """Produce deterministic chart-structure evidence for Oracle learning.

    This module is intentionally descriptive. It does not place trades and does
    not bypass Oracle execution, liquidity, forecast, risk, or governance gates.
    The features reflect the framework emphasized in Jack Schwager's introductory
    technical-analysis text: trend, trading ranges/support-resistance, pattern
    confirmation and failed breakouts, oscillators, stop placement, objectives,
    and systematic testing of rules.
    """
    if history is None or history.empty:
        return _empty("no history")

    close = _series(history, "Close")
    high = _series(history, "High")
    low = _series(history, "Low")
    if len(close) < max(trend_slow + 2, support_window + 3):
        return _empty("insufficient history")

    price = float(close.iloc[-1])
    if not np.isfinite(price) or price <= 0:
        return _empty("invalid price")

    fast = float(close.tail(trend_fast).mean())
    slow = float(close.tail(trend_slow).mean())
    recent_slope = float(close.iloc[-1] / close.iloc[-6] - 1.0) if len(close) >= 6 else 0.0
    ma_gap = (fast / slow - 1.0) if slow else 0.0
    trend_score = _clip((ma_gap / 0.05) * 0.65 + (recent_slope / 0.04) * 0.35)
    if trend_score > 0.20:
        trend_state = "uptrend"
    elif trend_score < -0.20:
        trend_state = "downtrend"
    else:
        trend_state = "range"

    prior_close = close.iloc[:-1]
    prior_high = high.iloc[:-1] if len(high) >= len(close) else prior_close
    prior_low = low.iloc[:-1] if len(low) >= len(close) else prior_close
    support = float(prior_low.tail(support_window).min())
    resistance = float(prior_high.tail(support_window).max())
    support_distance = (price / support - 1.0) if support > 0 else None
    resistance_distance = (resistance / price - 1.0) if resistance > 0 else None

    previous_price = float(close.iloc[-2])
    previous_resistance = float(prior_high.iloc[:-1].tail(support_window).max()) if len(prior_high) > 1 else resistance
    previous_support = float(prior_low.iloc[:-1].tail(support_window).min()) if len(prior_low) > 1 else support

    breakout_state = "none"
    breakout_score = 0.0
    failed_breakout = False
    pattern_tag = ""

    if price > resistance and resistance > 0:
        breakout_state = "upside_breakout"
        breakout_score = _clip((price / resistance - 1.0) / 0.03, 0.0, 1.0)
        pattern_tag = "range_breakout_up"
    elif price < support and support > 0:
        breakout_state = "downside_breakout"
        breakout_score = -_clip((support / price - 1.0) / 0.03, 0.0, 1.0)
        pattern_tag = "range_breakout_down"
    elif previous_price > previous_resistance > 0 and price <= previous_resistance:
        breakout_state = "failed_upside_breakout"
        breakout_score = -0.80
        failed_breakout = True
        pattern_tag = "failed_breakout_bearish"
    elif 0 < previous_price < previous_support and price >= previous_support:
        breakout_state = "failed_downside_breakout"
        breakout_score = 0.80
        failed_breakout = True
        pattern_tag = "failed_breakout_bullish"
    elif resistance_distance is not None and resistance_distance <= 0.01:
        breakout_state = "testing_resistance"
        breakout_score = 0.15 if trend_score > 0 else -0.10
        pattern_tag = "resistance_test"
    elif support_distance is not None and support_distance <= 0.01:
        breakout_state = "testing_support"
        breakout_score = -0.15 if trend_score < 0 else 0.10
        pattern_tag = "support_test"

    rsi_value = rsi(close)
    _, _, macd_hist = macd(close)
    macd_norm = macd_hist / max(price * 0.01, 1e-9)
    oscillator_score = _clip(
        ((50.0 - rsi_value) / 30.0) * 0.55 + _clip(macd_norm) * 0.45
    )
    if rsi_value <= 30 and macd_hist >= 0:
        oscillator_state = "oversold_recovery"
    elif rsi_value >= 70 and macd_hist <= 0:
        oscillator_state = "overbought_weakening"
    elif macd_hist > 0:
        oscillator_state = "positive_momentum"
    elif macd_hist < 0:
        oscillator_state = "negative_momentum"
    else:
        oscillator_state = "neutral"

    structure_alignment = trend_score * 0.45 + breakout_score * 0.35 + oscillator_score * 0.20
    setup_score = _clip(structure_alignment)

    atr_value = atr(history)
    atr_stop = max(atr_value * 1.5, price * 0.01)
    if setup_score >= 0:
        structural_stop = support if support > 0 and support < price else price - atr_stop
        suggested_stop = max(0.0, min(price - atr_stop * 0.5, structural_stop - atr_value * 0.25))
        range_height = max(0.0, resistance - support)
        objective = max(resistance, price + max(range_height, atr_value * 2.0))
    else:
        structural_stop = resistance if resistance > price else price + atr_stop
        suggested_stop = structural_stop + atr_value * 0.25
        range_height = max(0.0, resistance - support)
        objective = min(support, price - max(range_height, atr_value * 2.0)) if support > 0 else price - atr_value * 2.0

    risk = abs(price - suggested_stop) if suggested_stop is not None else 0.0
    reward = abs(objective - price) if objective is not None else 0.0
    rr = reward / risk if risk > 0 and reward > 0 else None

    reason = (
        f"Schwager TA: {trend_state}; support {support:.6g}; resistance {resistance:.6g}; "
        f"pattern {breakout_state}; RSI {rsi_value:.1f}; oscillator {oscillator_state}; "
        f"structure score {setup_score:+.2f}."
    )

    return SchwagerTechnicalAssessment(
        available=True,
        trend_state=trend_state,
        trend_score=trend_score,
        support=support,
        resistance=resistance,
        support_distance_pct=support_distance,
        resistance_distance_pct=resistance_distance,
        breakout_state=breakout_state,
        breakout_score=breakout_score,
        failed_breakout=failed_breakout,
        oscillator_state=oscillator_state,
        oscillator_score=oscillator_score,
        setup_score=setup_score,
        pattern_tag=pattern_tag,
        suggested_stop=suggested_stop,
        objective_1=objective,
        reward_risk_ratio=rr,
        reason=reason,
    )
