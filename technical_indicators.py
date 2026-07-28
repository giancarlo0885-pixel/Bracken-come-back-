from __future__ import annotations
import numpy as np, pandas as pd

def rsi(series: pd.Series, period=14) -> float:
    delta = series.diff()
    gains = delta.clip(lower=0).rolling(period).mean()
    losses = -delta.clip(upper=0).rolling(period).mean()
    rs = gains / losses.replace(0, np.nan)
    values = 100 - (100/(1+rs))
    return float(values.dropna().iloc[-1]) if not values.dropna().empty else 50.0

def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def macd(series: pd.Series) -> tuple[float,float,float]:
    line = ema(series,12)-ema(series,26)
    signal = ema(line,9)
    hist = line-signal
    return float(line.iloc[-1]), float(signal.iloc[-1]), float(hist.iloc[-1])

def atr(frame: pd.DataFrame, period=14) -> float:
    prev_close = frame["Close"].shift(1)
    tr = pd.concat([
        frame["High"]-frame["Low"],
        (frame["High"]-prev_close).abs(),
        (frame["Low"]-prev_close).abs(),
    ],axis=1).max(axis=1)
    val = tr.rolling(period).mean().dropna()
    return float(val.iloc[-1]) if not val.empty else 0.0

def bollinger_position(series: pd.Series, period=20, std_mult=2.0) -> float:
    mean = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = mean + std_mult*std
    lower = mean - std_mult*std
    denom = (upper-lower).iloc[-1]
    return float((series.iloc[-1]-lower.iloc[-1])/denom) if denom else 0.5
