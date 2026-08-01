from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Iterable

import pandas as pd
import yfinance as yf

from config import LIVE_POSITION_PRICE_WORKERS
from provider_router import route_history


@dataclass
class MarketSnapshot:
    symbol: str
    price: float
    change_pct: float
    volume: float
    timestamp: str
    provider: str = "unknown"
    interval: str = "1d"


def _normalize(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    frame = frame.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    frame = frame.loc[:, ~frame.columns.duplicated(keep="last")]
    keep = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in frame.columns]
    return frame[keep].dropna(subset=["Close"])


def finite_scalar(value: object) -> float | None:
    """Return the most recent finite numeric value from scalar/Series/DataFrame input."""
    if isinstance(value, pd.DataFrame):
        if value.empty:
            return None
        if len(value.columns) == 1:
            value = value.iloc[:, 0]
        else:
            value = value.stack()
    if isinstance(value, pd.Series):
        numeric = pd.to_numeric(value, errors="coerce")
        numeric = numeric[numeric.map(lambda item: item is not None and math.isfinite(float(item)))]
        if numeric.empty:
            return None
        return float(numeric.iloc[-1])
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _column(frame: pd.DataFrame, column: str) -> pd.Series:
    value = frame[column]
    if isinstance(value, pd.DataFrame):
        value = value.iloc[:, 0] if len(value.columns) == 1 else value.iloc[:, -1]
    return pd.to_numeric(value, errors="coerce")


def _download_yahoo(symbol: str, period: str, interval: str) -> pd.DataFrame:
    data = yf.download(
        symbol,
        period=period,
        interval=interval,
        auto_adjust=True,
        progress=False,
        threads=False,
        prepost=True,
    )
    return _normalize(data)


def get_history(symbol: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    routed = route_history(symbol, period, interval, _download_yahoo)
    frame = routed.frame
    frame.attrs["provider_route"] = routed.metadata()
    return frame


def _snapshot_from_history(symbol: str, history: pd.DataFrame, interval: str) -> MarketSnapshot | None:
    if history is None or history.empty:
        return None
    if "Close" not in history.columns:
        return None
    closes = _column(history, "Close")
    price = finite_scalar(closes)
    if price is None:
        return None
    valid_closes = closes[closes.map(lambda item: item is not None and math.isfinite(float(item)) if pd.notna(item) else False)]
    previous = finite_scalar(valid_closes.iloc[:-1]) if len(valid_closes) > 1 else price
    previous = previous if previous is not None else price
    change = ((price / previous) - 1) * 100 if previous else 0.0
    volume = finite_scalar(_column(history, "Volume")) if "Volume" in history.columns else None
    volume = volume if volume is not None else 0.0
    route = dict(history.attrs.get("provider_route") or {})
    fetched_at = str(route.get("fetched_at") or datetime.now(timezone.utc).isoformat())
    return MarketSnapshot(
        symbol=symbol,
        price=price,
        change_pct=change,
        volume=volume,
        timestamp=fetched_at,
        provider=str(route.get("provider") or "unknown"),
        interval=interval,
    )


def get_live_snapshot(symbol: str) -> MarketSnapshot | None:
    """Get the freshest practical quote, with graceful fallbacks.

    Intraday providers are attempted first. If an account plan does not expose
    intraday candles, the function falls back to slower candles rather than
    failing the worker pulse.
    """
    attempts = (("1d", "1m"), ("5d", "5m"), ("1mo", "1h"), ("5d", "1d"))
    for period, interval in attempts:
        try:
            history = get_history(symbol, period, interval)
            snapshot = _snapshot_from_history(symbol, history, interval)
            if snapshot is not None:
                return snapshot
        except Exception:
            continue
    return None


def get_snapshot(symbol: str) -> MarketSnapshot | None:
    history = get_history(symbol, "5d", "1d")
    return _snapshot_from_history(symbol, history, "1d")


def get_many_snapshots(symbols: Iterable[str], live: bool = False) -> dict[str, MarketSnapshot]:
    symbol_list = list(dict.fromkeys(str(symbol).strip().upper() for symbol in symbols if symbol))
    if not symbol_list:
        return {}

    fetcher = get_live_snapshot if live else get_snapshot
    results: dict[str, MarketSnapshot] = {}
    with ThreadPoolExecutor(max_workers=min(LIVE_POSITION_PRICE_WORKERS, len(symbol_list))) as executor:
        futures = {executor.submit(fetcher, symbol): symbol for symbol in symbol_list}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                snapshot = future.result()
            except Exception:
                snapshot = None
            if snapshot is not None:
                results[symbol] = snapshot
    return results
