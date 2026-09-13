"""Causal candle features and a versioned, research-only dip/rebound hypothesis."""
from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Any

import numpy as np
import pandas as pd

from technical_indicators import atr, bollinger_position, rsi_series

PATTERN_VERSION = 1
STRATEGY_VERSION = "dip_rebound_v1"
MIN_HISTORY = 64
# These are prospective experiment parameters, not fitted probabilities of profit.
MIN_DIP_ATR = 1.5
MAX_DIP_ATR = 5.0
MIN_REBOUND_ATR = 0.25
MAX_REBOUND_ATR = 1.5
MAX_HOLD_BARS = 12


def finite(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def interval_minutes(value: Any) -> int | None:
    match = re.fullmatch(r"([1-9][0-9]*)(m|h|d)", str(value or "").lower())
    return int(match[1]) * {"m": 1, "h": 60, "d": 1440}[match[2]] if match else None


def extract_entry_pattern(history: pd.DataFrame, *, asof: datetime | None = None) -> dict[str, Any]:
    """Use completed, contiguous, timezone-aware candles; never fill missing OHLCV."""
    attrs = getattr(history, "attrs", {}) or {}
    route = attrs.get("provider_route") or {}
    interval = route.get("interval") or route.get("source_interval") or attrs.get("interval") or attrs.get("source_interval")
    minutes = interval_minutes(interval)
    result: dict[str, Any] = {"version": PATTERN_VERSION, "available": False, "source_interval": interval}

    def unavailable(reason: str) -> dict[str, Any]:
        return dict(result, reason=reason)

    if minutes is None or not isinstance(history, pd.DataFrame):
        return unavailable("unknown_interval")
    if not isinstance(history.index, pd.DatetimeIndex) or history.index.tz is None:
        return unavailable("timezone_aware_bars_required")
    if not history.index.is_monotonic_increasing or history.index.has_duplicates:
        return unavailable("unordered_or_duplicate_bars")
    now = pd.Timestamp(asof or datetime.now(timezone.utc))
    if now.tzinfo is None:
        return unavailable("timezone_aware_asof_required")
    delta = pd.Timedelta(minutes=minutes)
    frame = history.loc[history.index + delta <= now].tail(MIN_HISTORY).copy()
    required = ["Open", "High", "Low", "Close", "Volume"]
    if len(frame) < MIN_HISTORY or any(key not in frame for key in required):
        return unavailable("insufficient_complete_ohlcv")
    if isinstance(frame.columns, pd.MultiIndex):
        return unavailable("ambiguous_ohlcv_columns")
    if not (frame.index.to_series().diff().dropna() == delta).all():
        return unavailable("gapped_bars")
    frame = frame[required].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(frame.to_numpy(dtype=float)).all():
        return unavailable("non_finite_ohlcv")
    if (frame[["Open", "High", "Low", "Close"]] <= 0).any().any() or (frame.Volume < 0).any():
        return unavailable("invalid_ohlcv")
    if ((frame.High < frame[["Open", "Close", "Low"]].max(axis=1)) | (frame.Low > frame[["Open", "Close", "High"]].min(axis=1))).any():
        return unavailable("inconsistent_candle_range")

    close = frame.Close
    trough_pos = len(frame) - 8 + int(np.argmin(frame.Low.iloc[-8:].to_numpy()))
    before_dip = frame.iloc[:-8]
    volatility = atr(before_dip)
    average_volume = float(frame.Volume.iloc[-21:-1].mean())
    if volatility <= 0 or average_volume <= 0:
        return unavailable("volatility_or_volume_unavailable")
    peak = float(frame.High.iloc[trough_pos-20:trough_pos].max())
    trough = float(frame.Low.iloc[trough_pos])
    price = float(close.iloc[-1])
    values = rsi_series(close)
    context_trend = float(before_dip.Close.tail(10).mean() / before_dip.Close.tail(30).mean() - 1)
    context_regime = 1 if context_trend >= 0.002 else -1 if context_trend <= -0.002 else 0
    baseline = close.iloc[-21:-1]
    dispersion = float(baseline.std(ddof=0))
    result.update({
        "available": True, "bar_minutes": minutes,
        "bar_end": (frame.index[-1] + delta).isoformat(),
        "bar_end_unix": (frame.index[-1] + delta).timestamp(),
        "price": price, "atr": volatility, "atr_pct": volatility / price,
        "rsi": float(values.iloc[-1]), "rsi_delta": float(values.iloc[-1] - values.iloc[-2]),
        "trough_rsi": float(values.iloc[trough_pos]),
        "dip_depth_atr": (peak - trough) / volatility,
        "rebound_atr": (price - trough) / volatility,
        "prior_peak": peak, "trough": trough, "bars_since_trough": len(frame) - 1 - trough_pos,
        "volume_ratio": float(frame.Volume.iloc[-1]) / average_volume,
        "bollinger_position": bollinger_position(close),
        "context_trend": context_trend, "context_regime": context_regime,
        "momentum_5bars": float(price / close.iloc[-6] - 1),
        "momentum_20bars": float(price / close.iloc[-21] - 1),
        "higher_low": bool(frame.Low.iloc[-1] > frame.Low.iloc[-2] > trough),
        "rising_closes": bool(close.iloc[-1] > close.iloc[-2] > close.iloc[-3]),
    })
    if dispersion > 0:
        result["zscore"] = (price - float(baseline.mean())) / dispersion
    result["reason"] = "observed_completed_bars"
    return result


# Memory uses normalized numeric features; metadata is compared exactly, not as distance.
FEATURE_SCALES = {
    "rsi": 100.0, "rsi_delta": 30.0, "trough_rsi": 100.0,
    "dip_depth_atr": 6.0, "rebound_atr": 3.0, "atr_pct": 0.1,
    "volume_ratio": 4.0, "bollinger_position": 1.0, "zscore": 4.0,
}
PATTERN_METADATA = ("pattern_schema", "pattern_bar_minutes", "pattern_context_regime")
PATTERN_FEATURE_KEYS = tuple(f"pattern_{key}" for key in FEATURE_SCALES) + PATTERN_METADATA + ("pattern_bar_end_unix",)


def pattern_memory_features(signal: Any) -> dict[str, float]:
    pattern = signal.get("entry_pattern") if isinstance(signal, dict) else getattr(signal, "entry_pattern", None)
    if not isinstance(pattern, dict) or pattern.get("available") is not True or pattern.get("version") != PATTERN_VERSION:
        return {}
    if any(finite(pattern.get(key)) is None for key in ("bar_minutes", "context_regime", "bar_end_unix")):
        return {}
    features = {"pattern_schema": float(PATTERN_VERSION), "pattern_bar_minutes": float(pattern["bar_minutes"]),
                "pattern_context_regime": float(pattern["context_regime"]), "pattern_bar_end_unix": float(pattern["bar_end_unix"])}
    for key, scale in FEATURE_SCALES.items():
        number = finite(pattern.get(key))
        if number is not None:
            features[f"pattern_{key}"] = max(-2.0, min(2.0, number / scale))
    return features


def compatible_pattern_memory(current: dict[str, float], historical: dict[str, float]) -> bool:
    if "pattern_schema" not in current:
        return True
    return all(key in historical and historical[key] == current[key] for key in PATTERN_METADATA) and all(
        key in historical for key in current if key.startswith("pattern_") and key != "pattern_bar_end_unix"
    )


def assess_dip_rebound(pattern: dict[str, Any]) -> dict[str, Any]:
    """Select a confirmed rebound, not merely a negative return or oversold RSI."""
    checks = {
        "complete_pattern": pattern.get("available") is True and pattern.get("version") == PATTERN_VERSION,
        "supported_interval": pattern.get("bar_minutes") in (5, 15),
    }
    needed = ("dip_depth_atr", "rebound_atr", "trough_rsi", "rsi_delta", "volume_ratio", "context_regime")
    if not all(finite(pattern.get(key)) is not None for key in needed):
        return {"strategy": STRATEGY_VERSION, "qualified": False, "reason": "missing_pattern_evidence"}
    checks.update({
        "dip_in_range": MIN_DIP_ATR <= pattern["dip_depth_atr"] <= MAX_DIP_ATR,
        "rebound_in_range": MIN_REBOUND_ATR <= pattern["rebound_atr"] <= MAX_REBOUND_ATR,
        "oversold_at_trough": pattern["trough_rsi"] <= 40,
        "rsi_recovering": pattern["rsi_delta"] >= 3,
        "higher_low": pattern.get("higher_low") is True,
        "rising_closes": pattern.get("rising_closes") is True,
        "recent_trough": pattern.get("bars_since_trough") in (2, 3, 4),
        "volume_confirmation": pattern["volume_ratio"] >= 1,
        "not_downtrend": pattern["context_regime"] >= 0,
    })
    failures = [name for name, passed in checks.items() if not passed]
    return {"strategy": STRATEGY_VERSION, "qualified": not failures,
            "reason": ",".join(failures) if failures else "confirmed_dip_rebound", "checks": checks}
