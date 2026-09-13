from __future__ import annotations
import numpy as np, pandas as pd


def _series(value) -> pd.Series:
    if isinstance(value, pd.DataFrame):
        value = value.iloc[:, -1]
    return pd.to_numeric(value, errors="coerce").dropna()


def rsi(series: pd.Series, period=14) -> float:
    """Return Wilder RSI with correct monotonic/flat-series handling.

    The previous implementation replaced zero average losses with NaN. That made
    a persistent rally fall through to the neutral 50 fallback instead of 100,
    which directly corrupted overbought/oversold evidence. Wilder smoothing is
    used here after a simple-average seed, matching the conventional RSI(14)
    definition while remaining deterministic for short test fixtures.
    """
    series = _series(series)
    period = max(1, int(period))
    if len(series) < period + 1:
        return 50.0

    delta = series.diff().dropna()
    gains = delta.clip(lower=0.0)
    losses = (-delta.clip(upper=0.0))

    avg_gain = float(gains.iloc[:period].mean())
    avg_loss = float(losses.iloc[:period].mean())
    for i in range(period, len(delta)):
        avg_gain = ((avg_gain * (period - 1)) + float(gains.iloc[i])) / period
        avg_loss = ((avg_loss * (period - 1)) + float(losses.iloc[i])) / period

    if avg_loss <= 0.0 and avg_gain <= 0.0:
        return 50.0
    if avg_loss <= 0.0:
        return 100.0
    if avg_gain <= 0.0:
        return 0.0

    rs = avg_gain / avg_loss
    value = 100.0 - (100.0 / (1.0 + rs))
    return float(max(0.0, min(100.0, value)))


def ema(series: pd.Series, span: int) -> pd.Series:
    series = _series(series)
    return series.ewm(span=span, adjust=False).mean()


def macd(series: pd.Series) -> tuple[float,float,float]:
    series = _series(series)
    line = ema(series,12)-ema(series,26)
    signal = ema(line,9)
    hist = line-signal
    return float(line.iloc[-1]), float(signal.iloc[-1]), float(hist.iloc[-1])


def atr(frame: pd.DataFrame, period=14) -> float:
    close = _series(frame["Close"])
    high = _series(frame["High"])
    low = _series(frame["Low"])
    aligned = pd.concat({"High": high, "Low": low, "Close": close}, axis=1).dropna()
    if aligned.empty:
        return 0.0
    prev_close = aligned["Close"].shift(1)
    tr = pd.concat([
        aligned["High"]-aligned["Low"],
        (aligned["High"]-prev_close).abs(),
        (aligned["Low"]-prev_close).abs(),
    ],axis=1).max(axis=1)
    val = tr.rolling(period).mean().dropna()
    return float(val.iloc[-1]) if not val.empty else 0.0


def bollinger_position(series: pd.Series, period=20, std_mult=2.0) -> float:
    series = _series(series)
    mean = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = mean + std_mult*std
    lower = mean - std_mult*std
    denom = (upper-lower).iloc[-1]
    return float((series.iloc[-1]-lower.iloc[-1])/denom) if denom else 0.5
