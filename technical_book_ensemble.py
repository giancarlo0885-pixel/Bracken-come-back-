from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import math
import pandas as pd

from technical_indicators import rsi, macd, atr


def _series(frame: pd.DataFrame, column: str) -> pd.Series:
    if frame is None or column not in frame:
        return pd.Series(dtype=float)
    value = frame[column]
    if isinstance(value, pd.DataFrame):
        value = value.iloc[:, -1]
    return pd.to_numeric(value, errors="coerce").dropna()


def _clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return max(low, min(high, number))


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b not in (0, 0.0) and math.isfinite(b) else default


@dataclass(frozen=True)
class TechnicalBookEnsembleAssessment:
    available: bool
    pring_cycle_momentum_score: float
    murphy_confirmation_score: float
    oneil_breakout_quality_score: float
    nison_candlestick_context_score: float
    bulkowski_pattern_quality_score: float
    shannon_multihorizon_alignment_score: float
    schwager_systematic_structure_score: float
    consensus_score: float
    agreement_count: int
    conflict_score: float
    bullish_votes: int
    bearish_votes: int
    neutral_votes: int
    metric_version: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _empty(reason: str) -> TechnicalBookEnsembleAssessment:
    return TechnicalBookEnsembleAssessment(
        available=False,
        pring_cycle_momentum_score=0.0,
        murphy_confirmation_score=0.0,
        oneil_breakout_quality_score=0.0,
        nison_candlestick_context_score=0.0,
        bulkowski_pattern_quality_score=0.0,
        shannon_multihorizon_alignment_score=0.0,
        schwager_systematic_structure_score=0.0,
        consensus_score=0.0,
        agreement_count=0,
        conflict_score=0.0,
        bullish_votes=0,
        bearish_votes=0,
        neutral_votes=7,
        metric_version="ta-seven-book-ensemble-v1",
        reason=reason,
    )


def assess_technical_book_ensemble(
    history: pd.DataFrame,
    *,
    schwager_score: float = 0.0,
    relative_strength: float | None = None,
) -> TechnicalBookEnsembleAssessment:
    """Measurement-only technical-analysis ensemble for Oracle paper learning.

    Each score is a deterministic hypothesis inspired by a distinct framework:
    Pring (trend/momentum/volume cycles), Murphy (multi-indicator confirmation),
    O'Neil (breakout + relative strength + volume), Nison (candlestick context),
    Bulkowski (pattern quality/failure awareness), Shannon (multi-horizon trend
    alignment), and Schwager (existing systematic chart-structure score).

    The ensemble never places orders and should not alter execution until its own
    post-cost, walk-forward paper evidence proves incremental value.
    """
    if history is None or history.empty:
        return _empty("no history")

    close = _series(history, "Close")
    high = _series(history, "High")
    low = _series(history, "Low")
    open_ = _series(history, "Open")
    volume = _series(history, "Volume")
    if len(close) < 60 or len(high) < 20 or len(low) < 20:
        return _empty("insufficient history")

    price = float(close.iloc[-1])
    if not math.isfinite(price) or price <= 0:
        return _empty("invalid price")

    rsi_now = float(rsi(close))
    _, _, macd_hist = macd(close)
    atr_value = max(float(atr(history)), 1e-12)
    atr_pct = atr_value / price

    sma10 = float(close.tail(10).mean())
    sma20 = float(close.tail(20).mean())
    sma30 = float(close.tail(30).mean())
    sma50 = float(close.tail(50).mean())
    roc5 = _safe_div(price, float(close.iloc[-6]), 1.0) - 1.0
    roc20 = _safe_div(price, float(close.iloc[-21]), 1.0) - 1.0
    roc50 = _safe_div(price, float(close.iloc[-51]), 1.0) - 1.0

    recent_high_20 = float(high.iloc[-21:-1].max()) if len(high) >= 21 else float(high.tail(20).max())
    recent_low_20 = float(low.iloc[-21:-1].min()) if len(low) >= 21 else float(low.tail(20).min())
    range_20 = max(recent_high_20 - recent_low_20, atr_value)
    breakout_up = (price - recent_high_20) / max(atr_value, 1e-12)
    breakout_down = (recent_low_20 - price) / max(atr_value, 1e-12)
    location = _safe_div(price - recent_low_20, range_20, 0.5)

    volume_ratio = 1.0
    if len(volume) >= 20:
        average_volume = float(volume.tail(20).mean())
        if average_volume > 0:
            volume_ratio = float(volume.iloc[-1] / average_volume)
    volume_impulse = _clip((volume_ratio - 1.0) / 1.5)

    # Pring: combine intermediate trend, rate-of-change, oscillator state, and
    # volume confirmation. Keep extreme RSI from being interpreted linearly.
    momentum_core = 0.45 * _clip(roc20 / 0.12) + 0.25 * _clip(roc5 / 0.05) + 0.20 * _clip((sma20 / sma50 - 1.0) / 0.08)
    oscillator = 0.0
    if 52 <= rsi_now <= 70:
        oscillator = (rsi_now - 52) / 18.0
    elif 30 <= rsi_now < 48:
        oscillator = -(48 - rsi_now) / 18.0
    elif rsi_now > 75:
        oscillator = -0.25
    elif rsi_now < 25:
        oscillator = 0.25
    pring = _clip(momentum_core + 0.10 * oscillator + 0.10 * volume_impulse)

    # Murphy: reward confirmation among trend, MACD, RSI, and volume rather than
    # trusting one indicator in isolation.
    trend_sign = _clip((sma10 / sma30 - 1.0) / 0.04)
    macd_sign = _clip(macd_hist / max(price * 0.006, 1e-12))
    rsi_sign = _clip((rsi_now - 50.0) / 20.0)
    murphy_components = (trend_sign, macd_sign, rsi_sign, volume_impulse)
    murphy = _clip(sum(murphy_components) / len(murphy_components))

    # O'Neil: use only the technical portion available to Oracle here—price
    # strength near/beyond highs, relative strength when supplied, and volume.
    proximity_to_high = _clip(1.0 - max(0.0, recent_high_20 - price) / max(range_20, 1e-12), 0.0, 1.0)
    rs_score = _clip(relative_strength if relative_strength is not None else roc20 / 0.15)
    breakout_strength = _clip(max(breakout_up, 0.0) / 2.0, 0.0, 1.0)
    oneil = _clip(0.40 * (2.0 * proximity_to_high - 1.0) + 0.30 * rs_score + 0.20 * volume_impulse + 0.10 * breakout_strength)

    # Nison: candle shape matters only in context. Long lower rejection near
    # support is positive; long upper rejection near resistance is negative.
    nison = 0.0
    if len(open_) and len(high) and len(low):
        o = float(open_.iloc[-1])
        h = float(high.iloc[-1])
        l = float(low.iloc[-1])
        c = price
        candle_range = max(h - l, 1e-12)
        body = abs(c - o) / candle_range
        lower_wick = (min(o, c) - l) / candle_range
        upper_wick = (h - max(o, c)) / candle_range
        near_support = _clip(1.0 - abs(price - recent_low_20) / max(2.0 * atr_value, 1e-12), 0.0, 1.0)
        near_resistance = _clip(1.0 - abs(price - recent_high_20) / max(2.0 * atr_value, 1e-12), 0.0, 1.0)
        rejection = lower_wick * near_support - upper_wick * near_resistance
        direction = (1.0 if c > o else -1.0 if c < o else 0.0) * body
        nison = _clip(0.70 * rejection + 0.30 * direction)

    # Bulkowski: track clean breakout quality and penalize immediate failed
    # breakouts. This is deliberately generic; performance must be learned from
    # Oracle's own data rather than copying historical book statistics.
    prior_close = float(close.iloc[-2])
    prior_high = float(high.iloc[-2])
    prior_low = float(low.iloc[-2])
    failed_up = prior_high > recent_high_20 and price < recent_high_20
    failed_down = prior_low < recent_low_20 and price > recent_low_20
    if failed_up:
        bulkowski = -0.8
    elif failed_down:
        bulkowski = 0.8
    elif breakout_up > 0:
        bulkowski = _clip(0.55 + 0.25 * _clip(breakout_up / 2.0) + 0.20 * volume_impulse)
    elif breakout_down > 0:
        bulkowski = _clip(-0.55 - 0.25 * _clip(breakout_down / 2.0) - 0.20 * volume_impulse)
    else:
        compression = _clip(1.0 - range_20 / max(price * 0.12, atr_value), 0.0, 1.0)
        directional_bias = _clip((location - 0.5) * 2.0)
        bulkowski = _clip(compression * directional_bias * 0.45)

    # Shannon: require alignment across several horizons. This uses independent
    # rolling horizons from the same feed; true cross-feed timeframe evidence can
    # be added later when all intervals are persisted together.
    horizon_votes = [
        1.0 if price > sma10 else -1.0,
        1.0 if sma10 > sma20 else -1.0,
        1.0 if sma20 > sma50 else -1.0,
        _clip(roc5 / 0.04),
        _clip(roc20 / 0.10),
        _clip(roc50 / 0.20),
    ]
    shannon = _clip(sum(horizon_votes) / len(horizon_votes))

    schwager = _clip(schwager_score)

    scores = (pring, murphy, oneil, nison, bulkowski, shannon, schwager)
    bullish = sum(score >= 0.25 for score in scores)
    bearish = sum(score <= -0.25 for score in scores)
    neutral = len(scores) - bullish - bearish
    agreement = max(bullish, bearish)
    mean_score = sum(scores) / len(scores)
    dispersion = math.sqrt(sum((score - mean_score) ** 2 for score in scores) / len(scores))
    conflict = _clip(dispersion / 0.75, 0.0, 1.0)

    # Consensus is intentionally discounted by disagreement. It is a metric for
    # learning/evaluation, not a trade authorization score.
    consensus = _clip(mean_score * (1.0 - 0.50 * conflict))
    reason = (
        "TA ensemble v1 | "
        f"Pring={pring:+.2f} Murphy={murphy:+.2f} ONeil={oneil:+.2f} "
        f"Nison={nison:+.2f} Bulkowski={bulkowski:+.2f} Shannon={shannon:+.2f} "
        f"Schwager={schwager:+.2f} consensus={consensus:+.2f} "
        f"votes=+{bullish}/-{bearish}/0{neutral} conflict={conflict:.2f}."
    )

    return TechnicalBookEnsembleAssessment(
        available=True,
        pring_cycle_momentum_score=round(pring, 6),
        murphy_confirmation_score=round(murphy, 6),
        oneil_breakout_quality_score=round(oneil, 6),
        nison_candlestick_context_score=round(nison, 6),
        bulkowski_pattern_quality_score=round(bulkowski, 6),
        shannon_multihorizon_alignment_score=round(shannon, 6),
        schwager_systematic_structure_score=round(schwager, 6),
        consensus_score=round(consensus, 6),
        agreement_count=agreement,
        conflict_score=round(conflict, 6),
        bullish_votes=bullish,
        bearish_votes=bearish,
        neutral_votes=neutral,
        metric_version="ta-seven-book-ensemble-v1",
        reason=reason,
    )
