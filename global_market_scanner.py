from __future__ import annotations

"""U.S. + crypto background discovery for GARIBALDI MARKET ORACLE.

The scanner is deliberately a funnel: it rotates through a broad in-scope
universe with inexpensive price/volume checks, persists the strongest movers,
and returns only a small active list to the full Oracle decision stack.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from typing import Any

import pandas as pd
import requests

from alpha_vantage_provider import symbol_search as alpha_symbol_search
from asset_routing import is_in_market_scope
from cache import cached_call
from config import API_CACHE_TTL_SECONDS
from config import (
    GLOBAL_CANDIDATE_TTL_SECONDS,
    GLOBAL_CORE_SYMBOLS_PER_CYCLE,
    GLOBAL_ETF_SYMBOLS_PER_CYCLE,
    GLOBAL_GAP_MOVER_MIN_CHANGE_PCT,
    GLOBAL_INCLUDE_PROVIDER_DISCOVERY,
    GLOBAL_MAJOR_MOVER_MIN_CHANGE_PCT,
    GLOBAL_UNUSUAL_VOLUME_MIN_RATIO,
    PENNY_STOCK_MAX_PRICE,
    PENNY_STOCK_MIN_AVG_DOLLAR_VOLUME,
    PENNY_STOCK_MIN_DAILY_VOLUME,
    PENNY_STOCK_MIN_PRICE,
    PENNY_STOCK_ENABLED,
    OTC_STOCKS_ENABLED,
)
from api_manager import get_api_settings
from database import connect, utc_now
from market_data import get_history, get_live_snapshot
from provider_router import _redact_url, normalize_symbol
from market_sessions import (
    completed_daily_bar_is_fresh,
    confirmed_us_listing,
    is_otc_exchange,
    normalize_exchange,
    parse_utc,
    quote_freshness_seconds,
    quote_is_fresh,
    market_session_state,
    latest_valid_bar_timestamp,
)

log = logging.getLogger("global-market-scanner")
_ORIGINAL_GET_LIVE_SNAPSHOT = get_live_snapshot

EODHD_API_KEY = os.getenv("EODHD_API_KEY", "").strip()
GLOBAL_SCANNER_ENABLED = os.getenv("GLOBAL_SCANNER_ENABLED", "true").lower() == "true"
GLOBAL_SCAN_SYMBOLS_PER_CYCLE = max(10, int(os.getenv("GLOBAL_SCAN_SYMBOLS_PER_CYCLE", "45")))
GLOBAL_ACTIVE_CANDIDATES = max(5, int(os.getenv("GLOBAL_ACTIVE_CANDIDATES", "20")))
GLOBAL_MIN_PRICE = float(os.getenv("GLOBAL_MIN_PRICE", "1.00"))
GLOBAL_MIN_AVG_DOLLAR_VOLUME = float(os.getenv("GLOBAL_MIN_AVG_DOLLAR_VOLUME", "5000000"))
GLOBAL_UNIVERSE_TTL_SECONDS = max(3600, int(os.getenv("GLOBAL_UNIVERSE_TTL_SECONDS", "86400")))
ALPHA_VANTAGE_DISCOVERY_KEYWORDS = [
    item.strip()
    for item in os.getenv("ALPHA_VANTAGE_DISCOVERY_KEYWORDS", "Apple,Microsoft,Nvidia,Shell,Toyota,ETF").split(",")
    if item.strip()
][:12]
CORE_STOCKS = {
    "GOOGL": ("Alphabet Class A", "United States", "mega_cap_core"),
    "GOOG": ("Alphabet Class C", "United States", "mega_cap_core"),
    "AMZN": ("Amazon", "United States", "mega_cap_core"),
    "AAPL": ("Apple", "United States", "mega_cap_core"),
    "MSFT": ("Microsoft", "United States", "mega_cap_core"),
    "NVDA": ("NVIDIA", "United States", "mega_cap_core"),
    "META": ("Meta", "United States", "mega_cap_core"),
    "AVGO": ("Broadcom", "United States", "mega_cap_core"),
    "TSLA": ("Tesla", "United States", "large_cap_core"),
}
ETF_SEEDS = {
    "SPY": ("S&P 500 ETF", "United States", "etf"),
    "QQQ": ("Nasdaq-100 ETF", "United States", "etf"),
    "IWM": ("Russell 2000 ETF", "United States", "etf"),
    "DIA": ("Dow Jones ETF", "United States", "etf"),
    "SMH": ("Semiconductor ETF", "United States", "etf"),
    "XLF": ("Financial ETF", "United States", "etf"),
    "XLE": ("Energy ETF", "United States", "etf"),
    "XLV": ("Healthcare ETF", "United States", "etf"),
}
DISCOVERY_SYMBOLS = {
    "large_cap": ["JPM", "LLY", "UNH", "V", "MA", "XOM", "COST", "HD"],
    "mid_cap": ["CELH", "DUOL", "RBLX", "DKNG", "TOST", "FIVE"],
    "small_cap": ["IONQ", "RKLB", "SOFI", "ACHR", "RXRX", "JOBY"],
    "qualified_penny": ["SOUN", "BBAI", "OPEN", "LUMN", "WULF", "BITF"],
}
QUALIFIED_PENNY_EXCHANGES = {
    "BBAI": "NYSE",
    "BITF": "NASDAQ",
    "LUMN": "NYSE",
    "OPEN": "NASDAQ",
    "SOUN": "NASDAQ",
    "WULF": "NASDAQ",
}

# EODHD exchange code -> Yahoo Finance suffix for in-scope U.S. listings.
EXCHANGES: dict[str, dict[str, str]] = {
    "US": {"region": "North America", "suffix": ""},
}


@dataclass
class GlobalCandidate:
    symbol: str
    name: str
    exchange: str
    region: str
    sector: str
    price: float
    change_1d_pct: float
    change_5d_pct: float
    daily_volume: float
    relative_volume: float
    avg_dollar_volume: float
    volatility_pct: float
    mover_score: float
    primary_category: str
    mover_tags: list[str]
    discovery_source: str
    discovery_timestamp: str
    quote_timestamp: str
    historical_bar_timestamp: str
    historical_bar_date: str
    fetched_at: str
    provider_fetched_at: str
    quote_provider: str
    history_provider: str
    market_session: str
    quote_verified: bool
    data_freshness_seconds: float | None
    category: str
    risk_bucket: str
    tradeable: bool
    scanned_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ensure_tables() -> None:
    with connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS global_market_candidates (
            symbol TEXT PRIMARY KEY, name TEXT, exchange TEXT, region TEXT, sector TEXT,
            price DOUBLE PRECISION, change_1d_pct DOUBLE PRECISION,
            change_5d_pct DOUBLE PRECISION, daily_volume DOUBLE PRECISION,
            relative_volume DOUBLE PRECISION,
            avg_dollar_volume DOUBLE PRECISION, volatility_pct DOUBLE PRECISION,
            mover_score DOUBLE PRECISION, category TEXT DEFAULT 'unknown',
            primary_category TEXT DEFAULT 'unknown', mover_tags JSONB DEFAULT '[]'::jsonb,
            discovery_source TEXT DEFAULT 'rotating_universe', discovery_timestamp TEXT,
            quote_timestamp TEXT, historical_bar_timestamp TEXT, historical_bar_date TEXT,
            fetched_at TEXT, provider_fetched_at TEXT,
            quote_provider TEXT, history_provider TEXT, market_session TEXT,
            quote_verified BOOLEAN DEFAULT FALSE,
            data_freshness_seconds DOUBLE PRECISION,
            risk_bucket TEXT DEFAULT 'standard', tradeable BOOLEAN DEFAULT TRUE,
            payload JSONB, scanned_at TEXT NOT NULL)""")
        for statement in (
            "ALTER TABLE global_market_candidates ADD COLUMN IF NOT EXISTS category TEXT DEFAULT 'unknown'",
            "ALTER TABLE global_market_candidates ADD COLUMN IF NOT EXISTS primary_category TEXT DEFAULT 'unknown'",
            "ALTER TABLE global_market_candidates ADD COLUMN IF NOT EXISTS mover_tags JSONB DEFAULT '[]'::jsonb",
            "ALTER TABLE global_market_candidates ADD COLUMN IF NOT EXISTS daily_volume DOUBLE PRECISION",
            "ALTER TABLE global_market_candidates ADD COLUMN IF NOT EXISTS discovery_source TEXT DEFAULT 'rotating_universe'",
            "ALTER TABLE global_market_candidates ADD COLUMN IF NOT EXISTS discovery_timestamp TEXT",
            "ALTER TABLE global_market_candidates ADD COLUMN IF NOT EXISTS quote_timestamp TEXT",
            "ALTER TABLE global_market_candidates ADD COLUMN IF NOT EXISTS historical_bar_timestamp TEXT",
            "ALTER TABLE global_market_candidates ADD COLUMN IF NOT EXISTS historical_bar_date TEXT",
            "ALTER TABLE global_market_candidates ADD COLUMN IF NOT EXISTS fetched_at TEXT",
            "ALTER TABLE global_market_candidates ADD COLUMN IF NOT EXISTS provider_fetched_at TEXT",
            "ALTER TABLE global_market_candidates ADD COLUMN IF NOT EXISTS quote_provider TEXT",
            "ALTER TABLE global_market_candidates ADD COLUMN IF NOT EXISTS history_provider TEXT",
            "ALTER TABLE global_market_candidates ADD COLUMN IF NOT EXISTS market_session TEXT",
            "ALTER TABLE global_market_candidates ADD COLUMN IF NOT EXISTS quote_verified BOOLEAN DEFAULT FALSE",
            "ALTER TABLE global_market_candidates ADD COLUMN IF NOT EXISTS data_freshness_seconds DOUBLE PRECISION",
            "ALTER TABLE global_market_candidates ADD COLUMN IF NOT EXISTS risk_bucket TEXT DEFAULT 'standard'",
            "ALTER TABLE global_market_candidates ADD COLUMN IF NOT EXISTS tradeable BOOLEAN DEFAULT TRUE",
        ):
            conn.execute(statement)
        conn.execute("""CREATE INDEX IF NOT EXISTS idx_global_candidates_score
            ON global_market_candidates(mover_score DESC, scanned_at DESC)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS global_scanner_status (
            id INTEGER PRIMARY KEY DEFAULT 1, cursor INTEGER NOT NULL DEFAULT 0,
            universe_size INTEGER NOT NULL DEFAULT 0, scanned_count INTEGER NOT NULL DEFAULT 0,
            active_count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'waiting',
            message TEXT, updated_at TEXT NOT NULL)""")


def _eodhd_exchange_symbols(exchange: str) -> list[dict[str, Any]]:
    if not EODHD_API_KEY:
        return []
    url = f"https://eodhd.com/api/exchange-symbol-list/{exchange}"
    try:
        response = requests.get(url, params={"api_token": EODHD_API_KEY, "fmt": "json"}, timeout=25)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []
    except Exception as exc:
        log.warning("EODHD universe request failed for %s: %s", exchange, _redact_url(str(exc)))
        return []


def _to_yahoo_symbol(code: str, exchange: str) -> str:
    code = str(code or "").strip().upper()
    if not code:
        return ""
    suffix = EXCHANGES.get(exchange, {}).get("suffix", "")
    if exchange == "HK":
        code = code.zfill(4)
    return f"{code}{suffix}"


def _load_universe() -> list[dict[str, str]]:
    universe: dict[str, dict[str, str]] = {}
    for symbol, data in CORE_STOCKS.items():
        universe[symbol] = {"symbol": symbol, "name": data[0], "exchange": "US", "region": data[1], "sector": data[2]}
    for symbol, data in ETF_SEEDS.items():
        universe[symbol] = {"symbol": symbol, "name": data[0], "exchange": "US", "region": data[1], "sector": data[2]}
    if GLOBAL_INCLUDE_PROVIDER_DISCOVERY:
        for category, symbols in DISCOVERY_SYMBOLS.items():
            for symbol in symbols:
                universe.setdefault(symbol, {
                    "symbol": symbol,
                    "name": symbol,
                    "exchange": QUALIFIED_PENNY_EXCHANGES.get(symbol, "US"),
                    "region": "United States",
                    "sector": category,
                })
        for keyword in ALPHA_VANTAGE_DISCOVERY_KEYWORDS:
            try:
                matches = cached_call(
                    f"alpha_vantage_symbol_search_{keyword.lower()}",
                    GLOBAL_UNIVERSE_TTL_SECONDS,
                    alpha_symbol_search,
                    keyword,
                )
            except Exception as exc:
                log.debug("Alpha Vantage symbol search unavailable for %s: %s", keyword, _redact_url(str(exc)))
                continue
            for item in matches[:15]:
                symbol = normalize_symbol(item.get("symbol"))
                if not symbol or len(symbol) > 24:
                    continue
                if not is_in_market_scope(symbol, exchange=item.get("region"), region=item.get("region")):
                    continue
                universe.setdefault(symbol, {
                    "symbol": symbol,
                    "name": str(item.get("name") or symbol),
                    "exchange": str(item.get("region") or "Unknown"),
                    "region": str(item.get("region") or "Unknown"),
                    "sector": str(item.get("type") or "Alpha Vantage discovery"),
                    "currency": str(item.get("currency") or ""),
                    "timezone": str(item.get("timezone") or ""),
                    "discovery_source": "alpha_vantage_symbol_search",
                })
    # Pull a bounded, liquid-looking common-stock universe from configured U.S. exchanges.
    for exchange, meta in EXCHANGES.items():
        if exchange != "US":
            continue
        for item in _eodhd_exchange_symbols(exchange):
            item_type = str(item.get("Type") or item.get("type") or "").lower()
            if item_type and not any(token in item_type for token in ("common", "stock", "ordinary")):
                continue
            symbol = _to_yahoo_symbol(item.get("Code") or item.get("code"), exchange)
            if not symbol or len(symbol) > 24:
                continue
            if not is_in_market_scope(symbol, exchange=exchange, region=meta["region"]):
                continue
            universe.setdefault(symbol, {
                "symbol": symbol,
                "name": str(item.get("Name") or item.get("name") or symbol),
                "exchange": exchange,
                "region": meta["region"],
                "sector": str(item.get("Sector") or item.get("sector") or "Unknown"),
            })
    return [item for item in universe.values() if is_in_market_scope(item.get("symbol"), exchange=item.get("exchange"), region=item.get("region"))]


def global_universe() -> list[dict[str, str]]:
    return cached_call("global_equity_universe", GLOBAL_UNIVERSE_TTL_SECONDS, _load_universe)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _series(frame: pd.DataFrame, column: str) -> pd.Series:
    value = frame[column]
    if isinstance(value, pd.DataFrame):
        value = value.iloc[:, -1]
    return pd.to_numeric(value, errors="coerce").dropna()


def _finite_number(value: Any) -> float | None:
    try:
        number = float(str(value).strip().replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _first_finite(*values: Any) -> float | None:
    for value in values:
        number = _finite_number(value)
        if number is not None:
            return number
    return None


def _provider_timestamp(value: Any) -> str:
    parsed = parse_utc(value)
    return parsed.isoformat() if parsed else ""


def _epoch_timestamp(value: Any) -> str:
    number = _finite_number(value)
    if number is None or number <= 0:
        return ""
    scale = 1
    if number > 10_000_000_000_000:
        scale = 1_000_000_000
    elif number > 10_000_000_000:
        scale = 1000
    try:
        return datetime.fromtimestamp(number / scale, timezone.utc).isoformat()
    except Exception:
        return ""


def _current_quote_from_meta(meta: dict[str, Any], now: datetime | None) -> dict[str, Any] | None:
    if meta.get("quote_verified") is not True:
        return None
    requested = normalize_symbol(meta.get("symbol"))
    if not requested:
        return None
    if (
        normalize_symbol(meta.get("requested_symbol")) != requested
        or normalize_symbol(meta.get("provider_symbol")) != requested
        or normalize_symbol(meta.get("symbol")) != requested
    ):
        return None
    price = _first_finite(meta.get("price"), meta.get("current_price"), meta.get("last_price"))
    change = _first_finite(meta.get("change_1d_pct"), meta.get("change_pct"), meta.get("percent_change"))
    volume = _first_finite(meta.get("daily_volume"), meta.get("volume"), meta.get("current_volume"))
    quote_timestamp = _provider_timestamp(meta.get("quote_timestamp") or meta.get("last_updated") or meta.get("timestamp"))
    if price is None or change is None or volume is None or not quote_timestamp:
        return None
    session = str(meta.get("market_session") or market_session_state(now, meta.get("exchange"), meta.get("region"), meta.get("symbol")))
    interval = "1m" if session in {"premarket", "regular", "after-hours"} else "1d"
    if not quote_is_fresh(
        quote_timestamp,
        interval,
        now,
        max_intraday_age_seconds=max(60, API_CACHE_TTL_SECONDS * 2),
        exchange=meta.get("exchange"),
        region=meta.get("region"),
        symbol=meta.get("symbol"),
    ):
        return None
    return {
        "price": price,
        "change_1d_pct": change,
        "daily_volume": volume,
        "relative_volume": _finite_number(meta.get("relative_volume")),
        "quote_timestamp": quote_timestamp,
        "quote_provider": str(meta.get("quote_provider") or meta.get("discovery_source") or "provider_snapshot"),
        "market_session": session,
        "quote_verified": True,
    }


def _current_quote_from_intraday(meta: dict[str, Any], now: datetime | None) -> dict[str, Any] | None:
    try:
        snapshot = get_live_snapshot(meta["symbol"])
    except Exception:
        return None
    if snapshot is None:
        return None
    if getattr(snapshot, "quote_verified", False) is not True:
        return None
    requested = normalize_symbol(meta.get("symbol"))
    snapshot_symbol = normalize_symbol(getattr(snapshot, "symbol", requested))
    snapshot_requested = normalize_symbol(getattr(snapshot, "requested_symbol", requested))
    snapshot_provider_symbol = normalize_symbol(getattr(snapshot, "provider_symbol", requested))
    if (
        snapshot_symbol != requested
        or snapshot_requested != requested
        or snapshot_provider_symbol != requested
    ):
        return None
    if _finite_number(snapshot.price) is None or float(snapshot.price) <= 0:
        return None
    if not quote_is_fresh(
        snapshot.timestamp,
        snapshot.interval,
        now,
        max_intraday_age_seconds=max(60, API_CACHE_TTL_SECONDS * 2),
        exchange=meta.get("exchange"),
        region=meta.get("region"),
        symbol=meta.get("symbol"),
    ):
        return None
    return {
        "price": snapshot.price,
        "change_1d_pct": snapshot.change_pct,
        "daily_volume": snapshot.volume,
        "relative_volume": None,
        "quote_timestamp": snapshot.timestamp,
        "quote_provider": snapshot.provider,
        "market_session": market_session_state(now, meta.get("exchange"), meta.get("region"), meta.get("symbol")),
        "quote_verified": True,
    }


def _current_quote_from_history(meta: dict[str, Any], hist: pd.DataFrame, now: datetime | None) -> dict[str, Any] | None:
    route = dict(getattr(hist, "attrs", {}).get("provider_route") or {})
    if route.get("quote_verified") is not True:
        return None
    requested = normalize_symbol(meta.get("symbol"))
    if (
        normalize_symbol(route.get("requested_symbol") or requested) != requested
        or normalize_symbol(route.get("provider_symbol") or requested) != requested
    ):
        return None
    bar = latest_valid_bar_timestamp(
        hist,
        "1d",
        exchange=meta.get("exchange"),
        region=meta.get("region"),
        symbol=meta.get("symbol"),
    )
    quote_time = bar.timestamp.isoformat() if bar else ""
    if not quote_is_fresh(
        quote_time,
        "1d",
        now,
        exchange=meta.get("exchange"),
        region=meta.get("region"),
        symbol=meta.get("symbol"),
    ):
        return None
    close = _series(hist, "Close")
    volume = _series(hist, "Volume") if "Volume" in hist.columns else pd.Series(index=hist.index, dtype=float)
    if len(close) < 2:
        return None
    price = float(close.iloc[-1])
    previous = float(close.iloc[-2])
    return {
        "price": price,
        "change_1d_pct": ((price / previous) - 1.0) * 100.0 if previous else 0.0,
        "daily_volume": float(volume.iloc[-1]) if len(volume) else 0.0,
        "relative_volume": None,
        "quote_timestamp": quote_time,
        "quote_provider": "daily_history",
        "market_session": market_session_state(now, meta.get("exchange"), meta.get("region"), meta.get("symbol")),
        "quote_verified": True,
    }


def _classify_candidate(meta: dict[str, str], price: float, change_1d: float, relative_volume: float, avg_dollar_volume: float, daily_volume: float) -> tuple[str, list[str], str, bool]:
    sector = str(meta.get("sector") or "").lower()
    if "etf" in sector:
        primary = "etf"
    elif price <= PENNY_STOCK_MAX_PRICE:
        primary = "penny_stock"
    elif "mega_cap" in sector or meta.get("symbol") in CORE_STOCKS:
        primary = "blue_chip"
    elif "large_cap" in sector:
        primary = "large_cap"
    elif "mid_cap" in sector:
        primary = "mid_cap"
    elif "small_cap" in sector:
        primary = "small_cap"
    else:
        primary = "dynamic_opportunity"
    tags: list[str] = []
    if change_1d >= GLOBAL_MAJOR_MOVER_MIN_CHANGE_PCT:
        tags.append("major_gainer")
    if change_1d <= -GLOBAL_MAJOR_MOVER_MIN_CHANGE_PCT:
        tags.append("major_loser")
    if abs(change_1d) >= GLOBAL_GAP_MOVER_MIN_CHANGE_PCT:
        tags.append("gap_mover")
    if relative_volume >= GLOBAL_UNUSUAL_VOLUME_MIN_RATIO:
        tags.append("unusual_volume")
    if price <= PENNY_STOCK_MAX_PRICE:
        tradeable = (
            PENNY_STOCK_ENABLED
            and price >= PENNY_STOCK_MIN_PRICE
            and daily_volume >= PENNY_STOCK_MIN_DAILY_VOLUME
            and avg_dollar_volume >= PENNY_STOCK_MIN_AVG_DOLLAR_VOLUME
            and confirmed_us_listing(meta.get("exchange"))
            and (OTC_STOCKS_ENABLED or not is_otc_exchange(meta.get("exchange")))
        )
        return primary, tags, "strict_penny_controls", tradeable
    return primary, tags, "standard", True


def _mover_meta(
    symbol: str,
    mover_type: str,
    source: str,
    name: str | None = None,
    exchange: Any = "",
    **metadata: Any,
) -> dict[str, Any]:
    normalized_exchange = normalize_exchange(exchange)
    return {
        "symbol": str(symbol or "").upper().strip(),
        "name": name or str(symbol or "").upper().strip(),
        "exchange": normalized_exchange,
        "region": "United States",
        "sector": mover_type,
        "discovery_source": source,
        "mover_type": mover_type,
        "discovery_timestamp": _now_iso(),
        **{key: value for key, value in metadata.items() if value not in (None, "", [])},
    }


def _tag_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in value.replace("|", ",").split(",") if part.strip()]
    return []


def merge_candidate_metadata(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in items:
        symbol = str(item.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        if not is_in_market_scope(symbol, exchange=item.get("exchange"), region=item.get("region")):
            continue
        current = merged.setdefault(symbol, {"symbol": symbol})
        for key, value in item.items():
            if value in (None, "", []):
                continue
            if key in {"mover_type", "mover_tags"}:
                existing_tags = _tag_list(current.get("mover_tags"))
                for tag in _tag_list(value) + ([str(value)] if key == "mover_type" and str(value).strip() else []):
                    if tag not in existing_tags:
                        existing_tags.append(tag)
                current["mover_tags"] = existing_tags
                if key == "mover_type" and not current.get("mover_type"):
                    current["mover_type"] = value
                continue
            if key == "discovery_source":
                sources = _tag_list(current.get("discovery_source"))
                for source in _tag_list(value):
                    if source not in sources:
                        sources.append(source)
                current[key] = ",".join(sources) if sources else value
                continue
            if key in {"discovery_timestamp", "quote_timestamp", "fetched_at", "provider_fetched_at", "historical_bar_timestamp"}:
                if not current.get(key):
                    current[key] = value
                else:
                    current_dt = parse_utc(current.get(key))
                    value_dt = parse_utc(value)
                    if value_dt and (current_dt is None or value_dt > current_dt):
                        current[key] = value
                continue
            if key == "quote_verified":
                current[key] = bool(current.get(key)) or bool(value)
                continue
            if key == "exchange":
                normalized = normalize_exchange(value)
                current_exchange = normalize_exchange(current.get("exchange"))
                if normalized and (not current_exchange or current_exchange == "US" or is_otc_exchange(normalized)):
                    current[key] = normalized
                continue
            if not current.get(key):
                current[key] = value
            elif key == "sector" and "core" in str(value).lower():
                current[key] = value
    return list(merged.values())


def _alpha_vantage_movers(key: str) -> list[dict[str, str]]:
    response = requests.get(
        "https://www.alphavantage.co/query",
        params={"function": "TOP_GAINERS_LOSERS", "apikey": key},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    out: list[dict[str, str]] = []
    if not isinstance(payload, dict) or "Note" in payload or "Information" in payload:
        raise RuntimeError("Alpha Vantage mover capability limited")
    for field, mover_type in (("top_gainers", "major_gainer"), ("top_losers", "major_loser"), ("most_actively_traded", "unusual_volume")):
        for item in payload.get(field, [])[:25]:
            symbol = item.get("ticker")
            if symbol:
                change = _finite_number(item.get("change_percentage"))
                out.append(_mover_meta(
                    symbol,
                    mover_type,
                    "alpha_vantage_top_gainers_losers",
                    exchange=item.get("exchange") or item.get("market") or item.get("exchangeCode"),
                    price=_finite_number(item.get("price")),
                    change_1d_pct=change,
                    daily_volume=_finite_number(item.get("volume")),
                    quote_provider="alpha_vantage_top_gainers_losers",
                    provider_fetched_at=_now_iso(),
                    quote_verified=False,
                    provider_metadata={"alpha_vantage": item},
                ))
    return out


def _polygon_snapshot_movers(key: str) -> list[dict[str, str]]:
    response = requests.get(
        "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers",
        params={"apiKey": key},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    tickers = payload.get("tickers", [])
    if not isinstance(tickers, list):
        return []
    ranked: list[tuple[float, dict[str, Any], str]] = []
    for item in tickers:
        symbol = item.get("ticker")
        day = item.get("day") or {}
        prev = item.get("prevDay") or {}
        last_trade = item.get("lastTrade") or {}
        updated_ms = item.get("updated") or last_trade.get("t") or day.get("t")
        close = float(day.get("c") or 0)
        prev_close = float(prev.get("c") or 0)
        volume = float(day.get("v") or 0)
        prev_volume = float(prev.get("v") or 0)
        change = ((close / prev_close) - 1.0) * 100.0 if close > 0 and prev_close > 0 else 0.0
        rel_volume = volume / prev_volume if prev_volume > 0 else 1.0
        if not symbol:
            continue
        if change >= GLOBAL_MAJOR_MOVER_MIN_CHANGE_PCT:
            ranked.append((abs(change), item, "major_gainer"))
        elif change <= -GLOBAL_MAJOR_MOVER_MIN_CHANGE_PCT:
            ranked.append((abs(change), item, "major_loser"))
        if abs(change) >= GLOBAL_GAP_MOVER_MIN_CHANGE_PCT:
            ranked.append((abs(change), item, "gap_mover"))
        if rel_volume >= GLOBAL_UNUSUAL_VOLUME_MIN_RATIO:
            ranked.append((rel_volume, item, "unusual_volume"))
    ranked.sort(key=lambda entry: entry[0], reverse=True)
    return [
        _mover_meta(
            item.get("ticker"), mover_type, "polygon_snapshot",
            exchange=item.get("primaryExchange") or item.get("exchange") or item.get("market"),
            price=_finite_number((item.get("lastTrade") or {}).get("p")) or _finite_number((item.get("day") or {}).get("c")),
            change_1d_pct=((_finite_number((item.get("day") or {}).get("c")) / _finite_number((item.get("prevDay") or {}).get("c")) - 1.0) * 100.0) if _finite_number((item.get("day") or {}).get("c")) and _finite_number((item.get("prevDay") or {}).get("c")) else None,
            daily_volume=_finite_number((item.get("day") or {}).get("v")),
            relative_volume=(_finite_number((item.get("day") or {}).get("v")) / _finite_number((item.get("prevDay") or {}).get("v"))) if _finite_number((item.get("day") or {}).get("v")) and _finite_number((item.get("prevDay") or {}).get("v")) else None,
            quote_timestamp=_epoch_timestamp(item.get("updated") or (item.get("lastTrade") or {}).get("t") or (item.get("day") or {}).get("t")),
            quote_provider="polygon_snapshot",
            provider_fetched_at=_now_iso(),
            quote_verified=bool(_epoch_timestamp(item.get("updated") or (item.get("lastTrade") or {}).get("t") or (item.get("day") or {}).get("t"))),
            provider_metadata={"polygon_snapshot": item},
        )
        for _, item, mover_type in ranked[:75]
    ]


def _eodhd_screener_movers(key: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for signal, mover_type in (("top_gainers", "major_gainer"), ("top_losers", "major_loser")):
        response = requests.get(
            "https://eodhd.com/api/screener",
            params={"api_token": key, "fmt": "json", "signals": signal, "limit": 25},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        for item in payload.get("data", []) if isinstance(payload, dict) else []:
            code = str(item.get("code") or item.get("symbol") or "").replace(".US", "")
            if code:
                exchange = item.get("exchange") or item.get("Exchange") or str(item.get("code") or "").split(".")[-1]
                out.append(_mover_meta(
                    code,
                    mover_type,
                    "eodhd_screener",
                    item.get("name"),
                    exchange=exchange,
                    price=_finite_number(item.get("price") or item.get("close")),
                    change_1d_pct=_finite_number(item.get("change_p") or item.get("change_pct") or item.get("change")),
                    daily_volume=_finite_number(item.get("volume")),
                    quote_provider="eodhd_screener",
                    provider_fetched_at=_now_iso(),
                    quote_verified=False,
                    provider_metadata={"eodhd": item},
                ))
    return out


def provider_mover_universe() -> list[dict[str, str]]:
    settings = get_api_settings()
    discovered: list[dict[str, Any]] = []
    for key_name, loader in (
        ("POLYGON_API_KEY", _polygon_snapshot_movers),
        ("ALPHA_VANTAGE_API_KEY", _alpha_vantage_movers),
        ("EODHD_API_KEY", _eodhd_screener_movers),
    ):
        key = settings.get(key_name)
        if not key:
            continue
        try:
            for meta in cached_call(f"mover_discovery_{key_name}", API_CACHE_TTL_SECONDS, loader, key):
                if meta.get("symbol"):
                    discovered.append(meta)
        except Exception as exc:
            log.debug("Mover discovery unavailable via %s: %s", key_name, _redact_url(str(exc)))
    return merge_candidate_metadata(discovered)


def filter_fresh_candidates(records: list[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    current = now or datetime.now(timezone.utc)
    fresh: list[dict[str, Any]] = []
    for record in records:
        scanned = parse_utc(record.get("scanned_at"))
        if scanned is None:
            continue
        if (current - scanned).total_seconds() <= GLOBAL_CANDIDATE_TTL_SECONDS:
            fresh.append(record)
    return sorted(
        fresh,
        key=lambda item: (float(item.get("mover_score") or 0), str(item.get("scanned_at") or "")),
        reverse=True,
    )


def _candidate_metrics(meta: dict[str, str], now: datetime | None = None) -> GlobalCandidate | None:
    if not is_in_market_scope(meta.get("symbol"), exchange=meta.get("exchange"), region=meta.get("region")):
        return None
    hist = get_history(meta["symbol"], "1mo", "1d")
    if hist is None or hist.empty or len(hist) < 6:
        return None
    close = _series(hist, "Close")
    if len(close) < 6:
        return None
    route = dict(hist.attrs.get("provider_route") or {})
    history_provider = str(route.get("provider") or "unknown")
    fetched_at = str(route.get("fetched_at") or utc_now())
    history_bar = latest_valid_bar_timestamp(
        hist,
        "1d",
        exchange=meta.get("exchange"),
        region=meta.get("region"),
        symbol=meta.get("symbol"),
    )
    historical_bar_timestamp = history_bar.timestamp.isoformat() if history_bar else ""
    historical_bar_date = history_bar.session_date.isoformat() if history_bar else ""
    intraday_quote = _current_quote_from_intraday(meta, now) if route or get_live_snapshot is not _ORIGINAL_GET_LIVE_SNAPSHOT else None
    current_quote = _current_quote_from_meta(meta, now) or intraday_quote or _current_quote_from_history(meta, hist, now)
    if current_quote is None:
        return None
    history_is_current = completed_daily_bar_is_fresh(
        historical_bar_timestamp,
        now,
        exchange=meta.get("exchange"),
        region=meta.get("region"),
        symbol=meta.get("symbol"),
    )
    if not history_is_current and current_quote.get("quote_provider") == "daily_history":
        return None
    price = float(current_quote["price"])
    penny_candidate = price <= PENNY_STOCK_MAX_PRICE
    if price < (PENNY_STOCK_MIN_PRICE if penny_candidate else GLOBAL_MIN_PRICE):
        return None
    volume = _series(hist, "Volume") if "Volume" in hist.columns else pd.Series(index=hist.index, dtype=float)
    volume = volume.fillna(0.0)
    avg_volume = float(volume.tail(20).mean()) if len(volume) else 0.0
    avg_dollar_volume = avg_volume * price
    min_dollar_volume = PENNY_STOCK_MIN_AVG_DOLLAR_VOLUME if penny_candidate else GLOBAL_MIN_AVG_DOLLAR_VOLUME
    if avg_dollar_volume < min_dollar_volume:
        return None
    change_1d = float(current_quote["change_1d_pct"])
    change_5d = ((price / float(close.iloc[-6])) - 1.0) * 100.0 if close.iloc[-6] else 0.0
    recent_avg_volume = float(volume.iloc[-6:-1].mean()) if len(volume) >= 6 else avg_volume
    quote_volume = float(current_quote["daily_volume"])
    relative_volume = float(current_quote["relative_volume"]) if current_quote.get("relative_volume") is not None else (quote_volume / recent_avg_volume if recent_avg_volume > 0 else 1.0)
    returns = close.pct_change().dropna().tail(20)
    volatility = float(returns.std() * 100.0) if not returns.empty else 0.0
    # Rewards decisive movement and participation without letting raw volatility dominate.
    mover_score = (
        min(35.0, abs(change_1d) * 5.0)
        + min(25.0, abs(change_5d) * 2.0)
        + min(25.0, max(0.0, relative_volume - 0.8) * 12.5)
        + min(10.0, max(0.0, volatility - 0.5) * 4.0)
        + min(5.0, max(0.0, (avg_dollar_volume / 50_000_000.0)))
    )
    daily_volume = quote_volume
    category, mover_tags, risk_bucket, tradeable = _classify_candidate(meta, price, change_1d, relative_volume, avg_dollar_volume, daily_volume)
    if not tradeable:
        return None
    quote_time = str(current_quote["quote_timestamp"])
    freshness = quote_freshness_seconds(quote_time, now)
    for explicit_mover in _tag_list(meta.get("mover_tags")) + _tag_list(meta.get("mover_type")):
        if explicit_mover and explicit_mover not in mover_tags:
            mover_tags.append(explicit_mover)
    market_session = str(current_quote.get("market_session") or market_session_state(now, meta.get("exchange"), meta.get("region"), meta.get("symbol")))
    if market_session in {"premarket", "after-hours"} and "extended_hours" not in mover_tags:
        mover_tags.append("extended_hours")
        risk_bucket = "extended_hours_high_risk" if risk_bucket == "standard" else f"{risk_bucket}_extended_hours"
    return GlobalCandidate(
        symbol=meta["symbol"], name=meta.get("name", meta["symbol"]),
        exchange=normalize_exchange(meta.get("exchange")) or "Unknown", region=meta.get("region", "Unknown"),
        sector=meta.get("sector", "Unknown"), price=price, change_1d_pct=change_1d,
        change_5d_pct=change_5d, daily_volume=daily_volume, relative_volume=relative_volume,
        avg_dollar_volume=avg_dollar_volume, volatility_pct=volatility,
        mover_score=round(mover_score, 3), primary_category=category,
        mover_tags=mover_tags, discovery_source=str(meta.get("discovery_source") or "rotating_universe"),
        discovery_timestamp=str(meta.get("discovery_timestamp") or utc_now()),
        quote_timestamp=quote_time or fetched_at, historical_bar_timestamp=historical_bar_timestamp,
        historical_bar_date=historical_bar_date, fetched_at=fetched_at,
        provider_fetched_at=str(meta.get("provider_fetched_at") or ""),
        quote_provider=str(current_quote.get("quote_provider") or "unknown"),
        history_provider=history_provider, market_session=market_session,
        quote_verified=bool(current_quote.get("quote_verified")),
        data_freshness_seconds=freshness, category=category,
        risk_bucket=risk_bucket, tradeable=tradeable, scanned_at=utc_now(),
    )


def scan_global_markets() -> list[dict[str, Any]]:
    """Scan the next rotating slice and return the strongest fresh candidates."""
    if not GLOBAL_SCANNER_ENABLED:
        return []
    _ensure_tables()
    universe = global_universe()
    if not universe:
        return []
    with connect() as conn:
        status = conn.execute("SELECT cursor FROM global_scanner_status WHERE id=1").fetchone()
        cursor = int((status or {}).get("cursor", 0) or 0)
    discovered_meta = provider_mover_universe()
    # Always include core stocks/ETFs, then rotate through the in-scope universe.
    seed_meta: list[dict[str, Any]] = []
    core_meta = [x for x in universe if x.get("symbol") in CORE_STOCKS][:GLOBAL_CORE_SYMBOLS_PER_CYCLE]
    etf_meta = [x for x in universe if x.get("symbol") in ETF_SEEDS][:GLOBAL_ETF_SYMBOLS_PER_CYCLE]
    rotating_count = max(1, GLOBAL_SCAN_SYMBOLS_PER_CYCLE - len(seed_meta) - len(core_meta) - len(etf_meta))
    rotating = [universe[(cursor + i) % len(universe)] for i in range(rotating_count)]
    batch = merge_candidate_metadata(seed_meta + core_meta + etf_meta + rotating + discovered_meta)
    found: list[GlobalCandidate] = []
    for meta in batch:
        try:
            candidate = _candidate_metrics(meta)
            if candidate:
                found.append(candidate)
        except Exception as exc:
            log.debug("Global candidate failed for %s: %s", meta.get("symbol"), _redact_url(str(exc)))
    with connect() as conn:
        for candidate in found:
            payload = candidate.to_dict()
            conn.execute("""INSERT INTO global_market_candidates
                (symbol,name,exchange,region,sector,price,change_1d_pct,change_5d_pct,
                 daily_volume,relative_volume,avg_dollar_volume,volatility_pct,mover_score,category,primary_category,
                 mover_tags,discovery_source,discovery_timestamp,quote_timestamp,historical_bar_timestamp,
                 historical_bar_date,fetched_at,provider_fetched_at,quote_provider,history_provider,
                 market_session,quote_verified,data_freshness_seconds,risk_bucket,tradeable,payload,scanned_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                ON CONFLICT (symbol) DO UPDATE SET name=EXCLUDED.name,exchange=EXCLUDED.exchange,
                region=EXCLUDED.region,sector=EXCLUDED.sector,price=EXCLUDED.price,
                change_1d_pct=EXCLUDED.change_1d_pct,change_5d_pct=EXCLUDED.change_5d_pct,
                daily_volume=EXCLUDED.daily_volume,
                relative_volume=EXCLUDED.relative_volume,avg_dollar_volume=EXCLUDED.avg_dollar_volume,
                volatility_pct=EXCLUDED.volatility_pct,mover_score=EXCLUDED.mover_score,
                category=EXCLUDED.category,primary_category=EXCLUDED.primary_category,
                mover_tags=EXCLUDED.mover_tags,discovery_source=EXCLUDED.discovery_source,
                discovery_timestamp=EXCLUDED.discovery_timestamp,quote_timestamp=EXCLUDED.quote_timestamp,
                historical_bar_timestamp=EXCLUDED.historical_bar_timestamp,
                historical_bar_date=EXCLUDED.historical_bar_date,
                fetched_at=EXCLUDED.fetched_at,provider_fetched_at=EXCLUDED.provider_fetched_at,
                quote_provider=EXCLUDED.quote_provider,history_provider=EXCLUDED.history_provider,
                market_session=EXCLUDED.market_session,quote_verified=EXCLUDED.quote_verified,
                data_freshness_seconds=EXCLUDED.data_freshness_seconds,
                risk_bucket=EXCLUDED.risk_bucket,tradeable=EXCLUDED.tradeable,
                payload=EXCLUDED.payload,scanned_at=EXCLUDED.scanned_at""",
                (candidate.symbol,candidate.name,candidate.exchange,candidate.region,candidate.sector,
                 candidate.price,candidate.change_1d_pct,candidate.change_5d_pct,candidate.daily_volume,candidate.relative_volume,
                 candidate.avg_dollar_volume,candidate.volatility_pct,candidate.mover_score,
                 candidate.category,candidate.primary_category,json.dumps(candidate.mover_tags),
                 candidate.discovery_source,candidate.discovery_timestamp,candidate.quote_timestamp,
                 candidate.historical_bar_timestamp,candidate.historical_bar_date,candidate.fetched_at,
                 candidate.provider_fetched_at,candidate.quote_provider,
                 candidate.history_provider,candidate.market_session,candidate.quote_verified,
                 candidate.data_freshness_seconds,
                 candidate.risk_bucket,candidate.tradeable,
                 json.dumps(payload),candidate.scanned_at))
        next_cursor = (cursor + rotating_count) % len(universe)
        conn.execute("""INSERT INTO global_scanner_status
            (id,cursor,universe_size,scanned_count,active_count,status,message,updated_at)
            VALUES (1,%s,%s,%s,%s,'healthy',%s,%s)
            ON CONFLICT (id) DO UPDATE SET cursor=EXCLUDED.cursor,universe_size=EXCLUDED.universe_size,
            scanned_count=EXCLUDED.scanned_count,active_count=EXCLUDED.active_count,
            status=EXCLUDED.status,message=EXCLUDED.message,updated_at=EXCLUDED.updated_at""",
            (next_cursor,len(universe),len(batch),len(found),
             f"Scanned {len(batch)} U.S./crypto symbols; {len(found)} passed liquidity and data checks.",utc_now()))
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=GLOBAL_CANDIDATE_TTL_SECONDS)).isoformat()
        conn.execute("DELETE FROM global_market_candidates WHERE scanned_at < %s", (cutoff,))
        rows = conn.execute("""SELECT symbol,name,exchange,region,sector,price,change_1d_pct,
            change_5d_pct,daily_volume,relative_volume,avg_dollar_volume,volatility_pct,mover_score,
            category,primary_category,mover_tags,discovery_source,discovery_timestamp,quote_timestamp,
            historical_bar_timestamp,historical_bar_date,fetched_at,provider_fetched_at,
            quote_provider,history_provider,market_session,quote_verified,
            data_freshness_seconds,risk_bucket,tradeable,scanned_at
            FROM global_market_candidates WHERE scanned_at >= %s
            ORDER BY mover_score DESC, scanned_at DESC LIMIT %s""",
            (cutoff, GLOBAL_ACTIVE_CANDIDATES,)).fetchall()
    return [dict(row) for row in rows]


def active_global_watchlist() -> dict[str, str]:
    try:
        candidates = scan_global_markets()
        return {str(x["symbol"]): str(x.get("name") or x["symbol"]) for x in candidates}
    except Exception as exc:
        log.exception("U.S./crypto scan failed: %s", exc)
        return {}
