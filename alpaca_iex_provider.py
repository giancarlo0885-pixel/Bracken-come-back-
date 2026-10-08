"""Read-only Alpaca IEX historical stock bars.

This module never submits orders. Credentials are read from the worker's environment.
The caller must validate provider provenance and candle freshness before using bars
for any trading decision.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os

import pandas as pd
import requests

_ALLOWED_INTERVALS = {"1m": "1Min", "5m": "5Min", "15m": "15Min", "30m": "30Min", "1h": "1Hour", "1d": "1Day"}
_PERIOD_DAYS = {"1d": 2, "5d": 7, "1mo": 35, "3mo": 100, "6mo": 190, "1y": 370}


class AlpacaDataError(RuntimeError):
    """Market-data request failed; never includes credential values."""


def iex_stock_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
    """Retrieve timestamped US equity bars from Alpaca's IEX feed.

    Returns an empty frame for an empty market-data result, not for auth,
    entitlement, rate-limit or network failures. No broker trading API is used.
    """
    key = os.getenv("APCA_API_KEY_ID", "").strip()
    secret = os.getenv("APCA_API_SECRET_KEY", "").strip()
    if not key or not secret:
        raise AlpacaDataError("Alpaca data credentials are not configured")
    if os.getenv("ALPACA_DATA_FEED", "iex").lower().strip() != "iex":
        raise AlpacaDataError("Only the Alpaca IEX feed is supported")
    url = os.getenv("ALPACA_DATA_URL", "https://data.alpaca.markets").rstrip("/")
    if url != "https://data.alpaca.markets":
        raise AlpacaDataError("Unexpected Alpaca market-data endpoint")
    symbol = str(symbol).upper().strip()
    if not symbol or not all(ch.isalnum() or ch in ".-" for ch in symbol):
        raise AlpacaDataError("Invalid stock symbol")
    timeframe = _ALLOWED_INTERVALS.get(interval)
    if timeframe is None:
        raise AlpacaDataError("Unsupported candle interval")
    days = _PERIOD_DAYS.get(period)
    if days is None:
        raise AlpacaDataError("Unsupported history period")
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    params = {
        "timeframe": timeframe,
        "start": start.isoformat(),
        "end": now.isoformat(),
        "feed": "iex",
        "adjustment": "raw",
        "limit": 10000,
        "sort": "asc",
    }
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    bars = []
    token = None
    try:
        for _ in range(5):
            if token:
                params["page_token"] = token
            response = requests.get(
                f"{url}/v2/stocks/{symbol}/bars",
                headers=headers,
                params=params,
                timeout=15,
            )
            if response.status_code != 200:
                raise AlpacaDataError(f"Alpaca market-data HTTP {response.status_code}")
            payload = response.json()
            bars.extend(payload.get("bars") or [])
            token = payload.get("next_page_token")
            if not token:
                break
        else:
            raise AlpacaDataError("Alpaca pagination limit reached")
    except (requests.RequestException, ValueError, TypeError) as exc:
        raise AlpacaDataError("Alpaca market-data request failed") from None
    if not bars:
        return pd.DataFrame()
    frame = pd.DataFrame(bars)
    required = {"t", "o", "h", "l", "c", "v"}
    if not required.issubset(frame.columns):
        raise AlpacaDataError("Alpaca bars missing required OHLCV fields")
    frame.index = pd.to_datetime(frame["t"], utc=True, errors="coerce")
    frame = frame.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
    frame = frame.loc[frame.index.notna(), ["Open", "High", "Low", "Close", "Volume"]]
    frame = frame.apply(pd.to_numeric, errors="coerce").dropna()
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    if frame.empty or (frame[["Open", "High", "Low", "Close"]] <= 0).any().any():
        raise AlpacaDataError("Alpaca returned invalid candle values")
    if (frame["High"] < frame[["Open", "Close", "Low"]].max(axis=1)).any() or (frame["Low"] > frame[["Open", "Close", "High"]].min(axis=1)).any() or (frame["Volume"] < 0).any():
        raise AlpacaDataError("Alpaca returned inconsistent OHLCV candles")
    frame.attrs["provider"] = "Alpaca IEX"
    frame.attrs["requested_symbol"] = symbol
    frame.attrs["provider_symbol"] = symbol
    frame.attrs["feed"] = "iex"
    return frame
