from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import math
from typing import Iterable

import pandas as pd
import yfinance as yf

from config import LIVE_POSITION_PRICE_WORKERS
from provider_router import normalize_symbol, route_history, verify_frame_symbol
from market_sessions import latest_valid_bar_timestamp

log = logging.getLogger("market-data")


@dataclass
class MarketSnapshot:
    symbol: str
    price: float
    change_pct: float
    volume: float
    timestamp: str
    provider: str = "unknown"
    interval: str = "1d"
    fetched_at: str | None = None
    requested_symbol: str | None = None
    provider_symbol: str | None = None
    quote_verified: bool = True


def _select_multiindex_symbol(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if frame is None or frame.empty or not isinstance(frame.columns, pd.MultiIndex):
        return frame
    requested = normalize_symbol(symbol)
    for level in range(frame.columns.nlevels):
        values = [normalize_symbol(value) for value in frame.columns.get_level_values(level)]
        if requested not in values:
            continue
        selected = frame.loc[:, [value == requested for value in values]].copy()
        selected.columns = selected.columns.droplevel(level)
        if isinstance(selected.columns, pd.MultiIndex) and selected.columns.nlevels == 1:
            selected.columns = selected.columns.get_level_values(0)
        return selected
    return pd.DataFrame()


def _normalize(frame: pd.DataFrame, symbol: str = "") -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    original_attrs = dict(getattr(frame, "attrs", {}) or {})
    frame = frame.copy(deep=True)
    if isinstance(frame.columns, pd.MultiIndex):
        frame = _select_multiindex_symbol(frame, symbol)
        if frame.empty:
            return pd.DataFrame()
    frame = frame.loc[:, ~frame.columns.duplicated(keep="last")]
    keep = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in frame.columns]
    frame = frame[keep].dropna(subset=["Close"])
    frame.attrs.update(original_attrs)
    return frame


def _stamp_history(frame: pd.DataFrame, requested_symbol: str, provider_symbol: str, provider: str, interval: str) -> pd.DataFrame:
    out = frame.copy(deep=True)
    out.attrs.clear()
    out.attrs.update(
        {
            "requested_symbol": normalize_symbol(requested_symbol),
            "provider_symbol": normalize_symbol(provider_symbol),
            "provider": provider,
            "interval": interval,
        }
    )
    return out


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


def latest_valid_index(frame: pd.DataFrame, column: str = "Close") -> datetime | None:
    if frame is None or frame.empty or column not in frame.columns:
        return None
    values = _column(frame, column)
    valid = values[values.map(lambda item: item is not None and math.isfinite(float(item)) if pd.notna(item) else False)]
    if valid.empty:
        return None
    index_value = valid.index[-1]
    try:
        timestamp = pd.Timestamp(index_value)
    except Exception:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(timezone.utc)
    return timestamp.to_pydatetime().astimezone(timezone.utc)


def latest_bar_timestamp(
    frame: pd.DataFrame,
    interval: str,
    exchange: str = "",
    region: str = "",
    symbol: str = "",
) -> datetime | None:
    route = dict(getattr(frame, "attrs", {}).get("provider_route") or {})
    bar = latest_valid_bar_timestamp(
        frame,
        interval,
        exchange=exchange or str(route.get("exchange") or ""),
        region=region or str(route.get("region") or ""),
        symbol=symbol,
        provider_timezone=route.get("timezone") or route.get("provider_timezone") or "",
    )
    return bar.timestamp if bar else None


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
    normalized = _normalize(data, symbol)
    if normalized.empty:
        return pd.DataFrame()
    return _stamp_history(normalized, symbol, symbol, "Yahoo Finance", interval)


def get_history(symbol: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    routed = route_history(symbol, period, interval, _download_yahoo)
    frame = routed.frame.copy(deep=True)
    route_metadata = routed.metadata()
    route_metadata.update(
        {
            "requested_symbol": normalize_symbol(symbol),
            "provider_symbol": normalize_symbol(frame.attrs.get("provider_symbol") or symbol),
            "interval": interval,
        }
    )
    quote_time = latest_bar_timestamp(frame, interval, symbol=symbol)
    if quote_time is not None and not route_metadata.get("quote_timestamp"):
        route_metadata["quote_timestamp"] = quote_time.isoformat()
    frame.attrs["provider_route"] = route_metadata
    return frame


def history_matches_symbol(history: pd.DataFrame, symbol: str) -> bool:
    if history is None or history.empty:
        return False
    if verify_frame_symbol(history, symbol):
        return True
    route = dict(getattr(history, "attrs", {}).get("provider_route") or {})
    requested = normalize_symbol(symbol)
    return (
        normalize_symbol(route.get("requested_symbol")) == requested
        and normalize_symbol(route.get("provider_symbol")) == requested
    )


def _snapshot_from_history(symbol: str, history: pd.DataFrame, interval: str) -> MarketSnapshot | None:
    if history is None or history.empty:
        return None
    if not history_matches_symbol(history, symbol):
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
    latest_quote = latest_bar_timestamp(history, interval, symbol=symbol)
    quote_timestamp = str(route.get("quote_timestamp") or (latest_quote.isoformat() if latest_quote else fetched_at))
    return MarketSnapshot(
        symbol=symbol,
        price=price,
        change_pct=change,
        volume=volume,
        timestamp=quote_timestamp,
        provider=str(route.get("provider") or "unknown"),
        interval=interval,
        fetched_at=fetched_at,
        requested_symbol=str(route.get("requested_symbol") or history.attrs.get("requested_symbol") or symbol),
        provider_symbol=str(route.get("provider_symbol") or history.attrs.get("provider_symbol") or ""),
        quote_verified=True,
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


def _duplicate_price_quarantine(snapshots: dict[str, MarketSnapshot]) -> set[str]:
    grouped: dict[tuple[str, float], list[str]] = {}
    for symbol, snapshot in snapshots.items():
        price = finite_scalar(snapshot.price)
        if price is None or price <= 0:
            continue
        provider = str(snapshot.provider or "unknown")
        grouped.setdefault((provider, price), []).append(symbol)
    quarantined: set[str] = set()
    for (provider, price), symbols in grouped.items():
        unrelated = sorted(set(symbols))
        if len(unrelated) < 3:
            continue
        quarantined.update(unrelated)
        log.warning(
            "Quarantined duplicate provider price anomaly | provider=%s price=%.6f affected_symbols=%d sample=%s",
            provider,
            price,
            len(unrelated),
            ",".join(unrelated[:8]),
        )
    return quarantined


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
                if (
                    normalize_symbol(snapshot.symbol) == symbol
                    and normalize_symbol(snapshot.requested_symbol) == symbol
                    and normalize_symbol(snapshot.provider_symbol) == symbol
                ):
                    results[symbol] = snapshot
    quarantined = _duplicate_price_quarantine(results)
    for symbol in quarantined:
        results.pop(symbol, None)
    return results
