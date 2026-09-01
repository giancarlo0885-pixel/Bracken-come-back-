from __future__ import annotations

from dataclasses import dataclass, asdict
import math
from typing import Any

import pandas as pd


TARGET_HORIZON_MINUTES = 15
MIN_ABS_ZSCORE = 1.25
MIN_MOVE_BPS = 20.0
MAX_SUPPORTED_BAR_MINUTES = 15


@dataclass(frozen=True)
class MeanReversionAssessment:
    available: bool
    side: str
    score: float
    confidence: float
    zscore: float | None
    horizon_return: float | None
    horizon_minutes: int
    horizon_bars: int
    dynamic_move_threshold: float | None
    displacement_bps: float | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _clip(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _interval_minutes(history: pd.DataFrame) -> int | None:
    route = dict(getattr(history, "attrs", {}).get("provider_route") or {})
    raw = str(route.get("interval") or getattr(history, "attrs", {}).get("interval") or "").strip().lower()
    if not raw:
        return None
    if raw.endswith("m") and raw[:-1].isdigit():
        return int(raw[:-1])
    return None


def _close_series(history: pd.DataFrame) -> pd.Series:
    if history is None or history.empty or "Close" not in history.columns:
        return pd.Series(dtype=float)
    close = history["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, -1]
    return pd.to_numeric(close, errors="coerce").dropna()


def assess_short_horizon_mean_reversion(
    symbol: str,
    history: pd.DataFrame,
    *,
    rsi_value: Any = None,
    atr_pct: Any = None,
    volume_ratio: Any = None,
    regime: str = "",
) -> MeanReversionAssessment:
    """Measure a crypto-only 15-minute directional reversal setup.

    The model intentionally treats short-horizon reversal as one factor, not a
    standalone trade authorization. Recent empirical work finds pervasive
    directional reversal in crypto around 15-minute horizons, but the measured
    gross edge can be smaller than ordinary spot round-trip costs. Oracle's
    existing quote, fee, liquidity, sizing, portfolio and risk gates therefore
    remain authoritative after this score is produced.
    """
    requested = str(symbol or "").upper().strip()
    if not requested.endswith("-USD"):
        return MeanReversionAssessment(False, "HOLD", 0.0, 0.0, None, None, 0, 0, None, None, "crypto USD pair required")

    bar_minutes = _interval_minutes(history)
    if bar_minutes is None or bar_minutes <= 0 or bar_minutes > MAX_SUPPORTED_BAR_MINUTES:
        return MeanReversionAssessment(False, "HOLD", 0.0, 0.0, None, None, 0, 0, None, None, "intraday bars of 15 minutes or less required")

    horizon_bars = max(1, round(TARGET_HORIZON_MINUTES / bar_minutes))
    horizon_minutes = horizon_bars * bar_minutes
    close = _close_series(history)
    lookback = max(20, horizon_bars * 6)
    if len(close) < max(lookback, horizon_bars + 2):
        return MeanReversionAssessment(False, "HOLD", 0.0, 0.0, None, None, horizon_minutes, horizon_bars, None, None, "insufficient intraday history")

    price = _finite(close.iloc[-1])
    prior = _finite(close.iloc[-(horizon_bars + 1)])
    if price is None or prior is None or price <= 0 or prior <= 0:
        return MeanReversionAssessment(False, "HOLD", 0.0, 0.0, None, None, horizon_minutes, horizon_bars, None, None, "invalid price history")

    baseline = close.iloc[-(lookback + 1):-1] if len(close) > lookback else close.iloc[:-1]
    mean = _finite(baseline.mean())
    std = _finite(baseline.std(ddof=0))
    if mean is None or std is None or std <= 0:
        return MeanReversionAssessment(False, "HOLD", 0.0, 0.0, None, None, horizon_minutes, horizon_bars, None, None, "rolling dispersion unavailable")

    zscore = (price - mean) / std
    horizon_return = (price / prior) - 1.0
    atr_fraction = max(0.0, _finite(atr_pct) or 0.0)
    dynamic_move_threshold = max(MIN_MOVE_BPS / 10_000.0, 0.75 * atr_fraction * math.sqrt(horizon_bars))
    displacement_bps = abs(price - mean) / price * 10_000.0 if price > 0 else 0.0

    side = "HOLD"
    direction = 0.0
    if horizon_return <= -dynamic_move_threshold and zscore <= -MIN_ABS_ZSCORE:
        side = "BUY"
        direction = 1.0
    elif horizon_return >= dynamic_move_threshold and zscore >= MIN_ABS_ZSCORE:
        side = "SELL"
        direction = -1.0

    if side == "HOLD":
        return MeanReversionAssessment(
            True,
            side,
            0.0,
            0.45,
            zscore,
            horizon_return,
            horizon_minutes,
            horizon_bars,
            dynamic_move_threshold,
            displacement_bps,
            "no statistically material short-horizon displacement",
        )

    z_strength = _clip((abs(zscore) - MIN_ABS_ZSCORE) / 1.75)
    move_ratio = abs(horizon_return) / dynamic_move_threshold if dynamic_move_threshold > 0 else 0.0
    move_strength = _clip((move_ratio - 1.0) / 2.0)
    strength = _clip(0.60 * z_strength + 0.40 * move_strength)

    rsi_number = _finite(rsi_value)
    confirmation = 1.0
    if rsi_number is not None:
        if side == "BUY" and rsi_number <= 40:
            confirmation += 0.10
        elif side == "BUY" and rsi_number >= 60:
            confirmation -= 0.25
        elif side == "SELL" and rsi_number >= 60:
            confirmation += 0.10
        elif side == "SELL" and rsi_number <= 40:
            confirmation -= 0.25

    volume_number = _finite(volume_ratio)
    if volume_number is not None:
        if volume_number >= 1.5:
            confirmation += 0.05
        elif volume_number < 0.50:
            confirmation -= 0.10

    # Falling-knife protection: a broad risk-off regime can make downside shocks
    # persist. Mean reversion remains informative but cannot dominate the engine.
    if side == "BUY" and str(regime or "").strip().lower() == "risk-off":
        confirmation *= 0.60

    signed_score = direction * _clip(strength * max(0.25, confirmation), 0.0, 1.0)
    confidence = _clip(0.45 + 0.35 * strength + 0.10 * max(0.0, confirmation - 1.0), 0.0, 0.90)
    return MeanReversionAssessment(
        True,
        side,
        signed_score,
        confidence,
        zscore,
        horizon_return,
        horizon_minutes,
        horizon_bars,
        dynamic_move_threshold,
        displacement_bps,
        "short-horizon downside reversal candidate" if side == "BUY" else "short-horizon upside reversion/exit candidate",
    )
