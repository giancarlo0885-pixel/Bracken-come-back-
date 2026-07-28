from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
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
    keep = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in frame.columns]
    return frame[keep].dropna(subset=["Close"])


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
    closes = history["Close"].astype(float)
    price = float(closes.iloc[-1])
    previous = float(closes.iloc[-2]) if len(closes) > 1 else price
    change = ((price / previous) - 1) * 100 if previous else 0.0
    volume = float(history["Volume"].iloc[-1]) if "Volume" in history.columns else 0.0
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
