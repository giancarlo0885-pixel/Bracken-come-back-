from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import math
from typing import Iterable

import pandas as pd
import yfinance as yf

from alpha_vantage_provider import global_quote as alpha_global_quote
from asset_routing import infer_asset_class
from config import (
    DECISION_CRYPTO_MAX_AGE_MINUTES,
    DECISION_STOCK_MAX_AGE_MINUTES,
    EXECUTION_MODE,
    LIVE_POSITION_PRICE_WORKERS,
    PAPER_BROKER_MODE,
)
from provider_router import normalize_symbol, route_history, verify_frame_symbol
from market_sessions import latest_valid_bar_timestamp, quote_is_fresh

log = logging.getLogger("market-data")


@dataclass
class MarketSnapshot:
    symbol: str
    price: float
    change_pct: float
    volume: float
    timestamp: str
    bid: float | None = None
    ask: float | None = None
    provider: str = "unknown"
    interval: str = "1d"
    fetched_at: str | None = None
    requested_symbol: str | None = None
    provider_symbol: str | None = None
    provider_native_symbol: str | None = None
    quote_verified: bool = False
    stale: bool = True
    spread_pct: float | None = None
    source_capability: str | None = None
    correlation_id: str | None = None
    source_identity: str | None = None
    cache_identity: str | None = None
    ohlcv_fingerprint: str | None = None
    provider_quote_verified: bool = False
    paper_reference_verified: bool = False
    verification_basis: str | None = None

    def to_quote_payload(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "requested_symbol": self.requested_symbol,
            "provider_symbol": self.provider_symbol,
            "provider_native_symbol": self.provider_native_symbol,
            "provider": self.provider,
            "price": self.price,
            "bid": self.bid,
            "ask": self.ask,
            "quote_timestamp": self.timestamp,
            "timestamp": self.timestamp,
            "interval": self.interval,
            "quote_verified": self.quote_verified,
            "verified": self.quote_verified,
            "stale": self.stale,
            "spread_pct": self.spread_pct,
            "source_capability": self.source_capability,
            "correlation_id": self.correlation_id,
            "decision_correlation_id": self.correlation_id,
            "source_identity": self.source_identity,
            "cache_identity": self.cache_identity,
            "ohlcv_fingerprint": self.ohlcv_fingerprint,
            "provider_quote_verified": self.provider_quote_verified,
            "paper_reference_verified": self.paper_reference_verified,
            "verification_basis": self.verification_basis,
        }


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
            "provider_native_symbol": normalize_symbol(provider_symbol),
            "provider": provider,
            "interval": interval,
        }
    )
    return out


def _ohlcv_fingerprint(frame: pd.DataFrame, rows: int = 5) -> str:
    if frame is None or frame.empty:
        return ""
    columns = [column for column in ("Open", "High", "Low", "Close", "Volume") if column in frame.columns]
    if not columns:
        return ""
    tail = frame[columns].tail(rows).copy()
    tail.index = [str(value) for value in tail.index]
    return str(hash(tuple(tuple(row) for row in tail.round(8).fillna("").itertuples(index=True, name=None))))


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


def _is_intraday_interval(interval: str) -> bool:
    text = str(interval or "").lower().strip()
    return bool(text) and not text.endswith("d") and text not in {"1wk", "1mo", "3mo", "1w", "weekly", "monthly"}


def _decision_max_age_seconds(symbol: str) -> int:
    minutes = DECISION_CRYPTO_MAX_AGE_MINUTES if infer_asset_class(symbol) == "crypto" else DECISION_STOCK_MAX_AGE_MINUTES
    return max(60, int(minutes) * 60)


def _paper_market_reference_eligible(
    *,
    symbol: str,
    interval: str,
    provider: str,
    requested_symbol: str,
    provider_symbol: str,
    price: object,
    quote_time: datetime | None,
) -> bool:
    """Allow a fresh Yahoo intraday mark for paper execution only.

    Primary execution-grade providers still win in provider_router. This path is
    intentionally limited to simulated paper trading and never upgrades daily
    bars or live-broker execution. Identity, positive price, and freshness must
    all pass before the fallback can be used.
    """
    if not PAPER_BROKER_MODE or str(EXECUTION_MODE or "").lower() != "paper":
        return False
    if str(provider or "").strip().lower() != "yahoo finance":
        return False
    if not _is_intraday_interval(interval):
        return False
    requested = normalize_symbol(symbol)
    if not requested or normalize_symbol(requested_symbol) != requested or normalize_symbol(provider_symbol) != requested:
        return False
    numeric_price = finite_scalar(price)
    if numeric_price is None or numeric_price <= 0 or quote_time is None:
        return False
    return quote_is_fresh(
        quote_time.isoformat(),
        interval,
        max_intraday_age_seconds=_decision_max_age_seconds(requested),
        symbol=requested,
    )


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
    latest_close = finite_scalar(_column(frame, "Close")) if "Close" in frame.columns else None
    provider_verified = frame.attrs.get("quote_verified") is True
    route_metadata.update(
        {
            "requested_symbol": normalize_symbol(symbol),
            "provider_symbol": normalize_symbol(frame.attrs.get("provider_symbol") or symbol),
            "provider_native_symbol": normalize_symbol(frame.attrs.get("provider_native_symbol") or frame.attrs.get("provider_symbol") or symbol),
            "period": period,
            "interval": interval,
            "price": latest_close,
            "current_price": latest_close,
            "source_identity": frame.attrs.get("source_identity"),
            "cache_identity": frame.attrs.get("cache_identity"),
            "ohlcv_fingerprint": _ohlcv_fingerprint(frame),
            "quote_verified": provider_verified,
            "provider_quote_verified": provider_verified,
            "paper_reference_verified": False,
            "verification_basis": "provider" if provider_verified else "unverified",
        }
    )
    quote_time = latest_bar_timestamp(frame, interval, symbol=symbol)
    if quote_time is not None and not route_metadata.get("quote_timestamp"):
        route_metadata["quote_timestamp"] = quote_time.isoformat()

    if _paper_market_reference_eligible(
        symbol=symbol,
        interval=interval,
        provider=str(route_metadata.get("provider") or ""),
        requested_symbol=str(route_metadata.get("requested_symbol") or ""),
        provider_symbol=str(route_metadata.get("provider_symbol") or ""),
        price=latest_close,
        quote_time=quote_time,
    ):
        route_metadata.update(
            {
                "quote_verified": True,
                "paper_reference_verified": True,
                "verification_basis": "paper:fresh_identity_matched_yahoo",
                "source_mode": "paper_market_reference",
                "source_capability": "history_intraday",
            }
        )
        frame.attrs["quote_verified"] = True
        frame.attrs["paper_reference_verified"] = True
        frame.attrs["provider_quote_verified"] = False
        frame.attrs["verification_basis"] = route_metadata["verification_basis"]
        frame.attrs["source_mode"] = route_metadata["source_mode"]
    else:
        frame.attrs["provider_quote_verified"] = provider_verified
        frame.attrs["paper_reference_verified"] = False
        frame.attrs["verification_basis"] = route_metadata["verification_basis"]

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
    requested_symbol = normalize_symbol(route.get("requested_symbol") or history.attrs.get("requested_symbol") or symbol)
    provider_symbol = normalize_symbol(route.get("provider_symbol") or history.attrs.get("provider_symbol") or "")
    provider_native_symbol = normalize_symbol(route.get("provider_native_symbol") or history.attrs.get("provider_native_symbol") or provider_symbol)
    provider = str(route.get("provider") or "unknown")
    period = str(route.get("period") or history.attrs.get("period") or "")
    cache_identity = str(route.get("cache_identity") or "")
    source_identity = str(route.get("source_identity") or "")
    if not source_identity and provider and requested_symbol and provider_symbol == requested_symbol and period:
        source_identity = f"{provider}:{requested_symbol}:{period}:{interval}"
    fetched_at = str(route.get("fetched_at") or datetime.now(timezone.utc).isoformat())
    latest_quote = latest_bar_timestamp(history, interval, symbol=symbol)
    quote_timestamp = str(route.get("quote_timestamp") or (latest_quote.isoformat() if latest_quote else ""))
    if not quote_timestamp:
        return None
    verified = route.get("quote_verified") is True
    return MarketSnapshot(
        symbol=symbol,
        price=price,
        change_pct=change,
        volume=volume,
        timestamp=quote_timestamp,
        bid=finite_scalar(route.get("bid")),
        ask=finite_scalar(route.get("ask")),
        provider=provider,
        interval=interval,
        fetched_at=fetched_at,
        requested_symbol=requested_symbol,
        provider_symbol=provider_symbol,
        provider_native_symbol=provider_native_symbol,
        quote_verified=verified,
        stale=not verified,
        spread_pct=finite_scalar(route.get("spread_pct")),
        source_capability=str(route.get("source_capability") or route.get("capability") or ("history_intraday" if interval not in {"1d", "daily"} else "history_daily")),
        correlation_id=str(route.get("correlation_id") or route.get("decision_correlation_id") or ""),
        source_identity=source_identity,
        cache_identity=cache_identity,
        ohlcv_fingerprint=str(route.get("ohlcv_fingerprint") or _ohlcv_fingerprint(history)),
        provider_quote_verified=route.get("provider_quote_verified") is True,
        paper_reference_verified=route.get("paper_reference_verified") is True,
        verification_basis=str(route.get("verification_basis") or ""),
    )


def _alpha_vantage_delayed_snapshot(symbol: str) -> MarketSnapshot | None:
    if infer_asset_class(symbol) == "crypto":
        return None
    quote = alpha_global_quote(symbol)
    if not quote:
        return None
    requested = normalize_symbol(symbol)
    provider_symbol = normalize_symbol(quote.get("provider_symbol"))
    if requested != provider_symbol:
        return None
    price = finite_scalar(quote.get("price"))
    if price is None or price <= 0:
        return None
    fetched_at = str(quote.get("provider_fetched_at") or datetime.now(timezone.utc).isoformat())
    latest_day = str(quote.get("latest_trading_day") or fetched_at)
    return MarketSnapshot(
        symbol=requested,
        price=price,
        change_pct=float(quote.get("change_pct") or 0.0),
        volume=float(quote.get("volume") or 0.0),
        timestamp=latest_day,
        bid=None,
        ask=None,
        provider="Alpha Vantage",
        interval="1d",
        fetched_at=fetched_at,
        requested_symbol=requested,
        provider_symbol=provider_symbol,
        provider_native_symbol=provider_symbol,
        quote_verified=False,
        stale=True,
        spread_pct=None,
        source_capability="global_quote_eod_fallback",
        correlation_id=str(quote.get("correlation_id") or ""),
        source_identity=f"Alpha Vantage:{requested}:GLOBAL_QUOTE:delayed",
        cache_identity=f"alpha_vantage_global_quote:{requested}",
        provider_quote_verified=False,
        paper_reference_verified=False,
        verification_basis="delayed_fallback",
    )


def get_live_snapshot(symbol: str) -> MarketSnapshot | None:
    """Return the freshest verified quote available, with a safe research fallback.

    A provider route may legitimately return an unverified research-only snapshot
    (for example Yahoo Finance). Do not let that result short-circuit later
    intervals that can still produce a verified execution-grade quote.
    """
    attempts = (("1d", "1m"), ("5d", "5m"), ("1mo", "1h"), ("5d", "1d"))
    research_fallback: MarketSnapshot | None = None
    for period, interval in attempts:
        try:
            history = get_history(symbol, period, interval)
            snapshot = _snapshot_from_history(symbol, history, interval)
        except Exception:
            continue
        if snapshot is None:
            continue
        if snapshot_is_verified(snapshot, symbol):
            return snapshot
        if research_fallback is None:
            research_fallback = snapshot

    # Preserve the best history result for research callers. Execution callers
    # still enforce snapshot_is_verified() in get_many_snapshots, so this does
    # not weaken identity/freshness gates. Alpha's delayed quote is only used
    # when every history route failed to produce a snapshot at all.
    if research_fallback is not None:
        return research_fallback
    try:
        return _alpha_vantage_delayed_snapshot(symbol)
    except Exception:
        return None


def get_snapshot(symbol: str) -> MarketSnapshot | None:
    history = get_history(symbol, "5d", "1d")
    return _snapshot_from_history(symbol, history, "1d")


def snapshot_is_verified(snapshot: MarketSnapshot | None, symbol: str) -> bool:
    if snapshot is None:
        return False
    requested = normalize_symbol(symbol)
    if (
        normalize_symbol(snapshot.symbol) != requested
        or normalize_symbol(snapshot.requested_symbol) != requested
        or normalize_symbol(snapshot.provider_symbol) != requested
    ):
        return False
    if snapshot.quote_verified is not True:
        return False
    price = finite_scalar(snapshot.price)
    if price is None or price <= 0:
        return False
    return quote_is_fresh(
        snapshot.timestamp,
        snapshot.interval,
        max_intraday_age_seconds=_decision_max_age_seconds(requested),
        symbol=requested,
    )


def _duplicate_price_quarantine(snapshots: dict[str, MarketSnapshot]) -> set[str]:
    grouped: dict[tuple[str, str], list[str]] = {}
    for symbol, snapshot in snapshots.items():
        price = finite_scalar(snapshot.price)
        if price is None or price <= 0:
            continue
        provider = str(snapshot.provider or "unknown")
        identities = [
            str(snapshot.cache_identity or ""),
            str(snapshot.source_identity or ""),
            str(snapshot.ohlcv_fingerprint or ""),
        ]
        for identity in identities:
            if identity:
                grouped.setdefault((provider, identity), []).append(symbol)
    quarantined: set[str] = set()
    for (provider, identity), symbols in grouped.items():
        unrelated = sorted(set(symbols))
        if len(unrelated) < 2:
            continue
        quarantined.update(unrelated)
        log.warning(
            "Quarantined duplicate provider/cache anomaly | provider=%s identity=%s affected_symbols=%d sample=%s",
            provider,
            identity[:80],
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
                if snapshot_is_verified(snapshot, symbol):
                    results[symbol] = snapshot
    quarantined = _duplicate_price_quarantine(results)
    for symbol in quarantined:
        results.pop(symbol, None)
    return results
