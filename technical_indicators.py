from __future__ import annotations
import numpy as np, pandas as pd

def _series(value) -> pd.Series:
    if isinstance(value, pd.DataFrame):
        value = value.iloc[:, -1]
    return pd.to_numeric(value, errors="coerce").dropna()

def rsi(series: pd.Series, period=14) -> float:
    series = _series(series)
    delta = series.diff()
    gains = delta.clip(lower=0).rolling(period).mean()
    losses = -delta.clip(upper=0).rolling(period).mean()
    rs = gains / losses.replace(0, np.nan)
    values = 100 - (100/(1+rs))
    return float(values.dropna().iloc[-1]) if not values.dropna().empty else 50.0

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
