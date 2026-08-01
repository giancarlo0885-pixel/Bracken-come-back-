from __future__ import annotations

"""Worldwide background discovery for GARIBALDI MARKET ORACLE.

The scanner is deliberately a funnel: it rotates through a broad international
universe with inexpensive price/volume checks, persists the strongest movers,
and returns only a small active list to the full Oracle decision stack.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
import os
from typing import Any

import pandas as pd
import requests

from cache import cached_call
from config import API_CACHE_TTL_SECONDS
from config import (
    GLOBAL_CORE_SYMBOLS_PER_CYCLE,
    GLOBAL_ETF_SYMBOLS_PER_CYCLE,
    GLOBAL_GAP_MOVER_MIN_CHANGE_PCT,
    GLOBAL_INCLUDE_PROVIDER_DISCOVERY,
    GLOBAL_MAJOR_MOVER_MIN_CHANGE_PCT,
    GLOBAL_UNUSUAL_VOLUME_MIN_RATIO,
    PENNY_STOCK_MAX_PRICE,
    PENNY_STOCK_MIN_AVG_DOLLAR_VOLUME,
    PENNY_STOCK_MIN_PRICE,
)
from database import connect, utc_now
from market_data import get_history

log = logging.getLogger("global-market-scanner")

EODHD_API_KEY = os.getenv("EODHD_API_KEY", "").strip()
GLOBAL_SCANNER_ENABLED = os.getenv("GLOBAL_SCANNER_ENABLED", "true").lower() == "true"
GLOBAL_SCAN_SYMBOLS_PER_CYCLE = max(10, int(os.getenv("GLOBAL_SCAN_SYMBOLS_PER_CYCLE", "45")))
GLOBAL_ACTIVE_CANDIDATES = max(5, int(os.getenv("GLOBAL_ACTIVE_CANDIDATES", "20")))
GLOBAL_MIN_PRICE = float(os.getenv("GLOBAL_MIN_PRICE", "1.00"))
GLOBAL_MIN_AVG_DOLLAR_VOLUME = float(os.getenv("GLOBAL_MIN_AVG_DOLLAR_VOLUME", "5000000"))
GLOBAL_UNIVERSE_TTL_SECONDS = max(3600, int(os.getenv("GLOBAL_UNIVERSE_TTL_SECONDS", "86400")))
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

# EODHD exchange code -> Yahoo Finance suffix. US/Canada use special handling.
EXCHANGES: dict[str, dict[str, str]] = {
    "US": {"region": "North America", "suffix": ""},
    "TO": {"region": "Canada", "suffix": ".TO"},
    "LSE": {"region": "United Kingdom", "suffix": ".L"},
    "XETRA": {"region": "Europe", "suffix": ".DE"},
    "PA": {"region": "Europe", "suffix": ".PA"},
    "AS": {"region": "Europe", "suffix": ".AS"},
    "SW": {"region": "Europe", "suffix": ".SW"},
    "MI": {"region": "Europe", "suffix": ".MI"},
    "MC": {"region": "Europe", "suffix": ".MC"},
    "ST": {"region": "Nordics", "suffix": ".ST"},
    "CO": {"region": "Nordics", "suffix": ".CO"},
    "HE": {"region": "Nordics", "suffix": ".HE"},
    "OL": {"region": "Nordics", "suffix": ".OL"},
    "TSE": {"region": "Japan", "suffix": ".T"},
    "HK": {"region": "Greater China", "suffix": ".HK"},
    "SHG": {"region": "China", "suffix": ".SS"},
    "SHE": {"region": "China", "suffix": ".SZ"},
    "NSE": {"region": "India", "suffix": ".NS"},
    "BSE": {"region": "India", "suffix": ".BO"},
    "AU": {"region": "Australia", "suffix": ".AX"},
    "JSE": {"region": "Africa", "suffix": ".JO"},
    "SA": {"region": "Latin America", "suffix": ".SA"},
    "MX": {"region": "Latin America", "suffix": ".MX"},
    "TA": {"region": "Middle East", "suffix": ".TA"},
}

# Reliable liquid leaders keep every region represented even if an exchange-list
# request is temporarily unavailable or the user's EODHD plan is limited.
GLOBAL_SEEDS: dict[str, tuple[str, str, str]] = {
    "SAP.DE": ("SAP", "Europe", "Technology"),
    "SIE.DE": ("Siemens", "Europe", "Industrials"),
    "ASML.AS": ("ASML", "Europe", "Semiconductors"),
    "MC.PA": ("LVMH", "Europe", "Consumer"),
    "SHEL.L": ("Shell", "United Kingdom", "Energy"),
    "AZN.L": ("AstraZeneca", "United Kingdom", "Healthcare"),
    "HSBA.L": ("HSBC", "United Kingdom", "Financials"),
    "NESN.SW": ("Nestle", "Europe", "Consumer Staples"),
    "NOVN.SW": ("Novartis", "Europe", "Healthcare"),
    "7203.T": ("Toyota", "Japan", "Automotive"),
    "6758.T": ("Sony", "Japan", "Technology"),
    "9984.T": ("SoftBank Group", "Japan", "Technology"),
    "005930.KS": ("Samsung Electronics", "South Korea", "Technology"),
    "000660.KS": ("SK Hynix", "South Korea", "Semiconductors"),
    "0700.HK": ("Tencent", "Greater China", "Technology"),
    "9988.HK": ("Alibaba", "Greater China", "Consumer Technology"),
    "3690.HK": ("Meituan", "Greater China", "Consumer Technology"),
    "RELIANCE.NS": ("Reliance Industries", "India", "Conglomerate"),
    "TCS.NS": ("Tata Consultancy Services", "India", "Technology"),
    "HDFCBANK.NS": ("HDFC Bank", "India", "Financials"),
    "BHP.AX": ("BHP", "Australia", "Materials"),
    "CBA.AX": ("Commonwealth Bank", "Australia", "Financials"),
    "SHOP.TO": ("Shopify", "Canada", "Technology"),
    "RY.TO": ("Royal Bank of Canada", "Canada", "Financials"),
    "VALE3.SA": ("Vale", "Latin America", "Materials"),
    "PETR4.SA": ("Petrobras", "Latin America", "Energy"),
    "NPN.JO": ("Naspers", "Africa", "Technology"),
    "TEVA.TA": ("Teva", "Middle East", "Healthcare"),
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
    relative_volume: float
    avg_dollar_volume: float
    volatility_pct: float
    mover_score: float
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
            change_5d_pct DOUBLE PRECISION, relative_volume DOUBLE PRECISION,
            avg_dollar_volume DOUBLE PRECISION, volatility_pct DOUBLE PRECISION,
            mover_score DOUBLE PRECISION, category TEXT DEFAULT 'unknown',
            risk_bucket TEXT DEFAULT 'standard', tradeable BOOLEAN DEFAULT TRUE,
            payload JSONB, scanned_at TEXT NOT NULL)""")
        for statement in (
            "ALTER TABLE global_market_candidates ADD COLUMN IF NOT EXISTS category TEXT DEFAULT 'unknown'",
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
        log.warning("EODHD universe request failed for %s: %s", exchange, exc)
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
    universe: dict[str, dict[str, str]] = {
        symbol: {"symbol": symbol, "name": data[0], "exchange": "SEED", "region": data[1], "sector": data[2]}
        for symbol, data in GLOBAL_SEEDS.items()
    }
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
                    "exchange": "US",
                    "region": "United States",
                    "sector": category,
                })
    # Pull a bounded, liquid-looking common-stock universe from each configured exchange.
    for exchange, meta in EXCHANGES.items():
        for item in _eodhd_exchange_symbols(exchange):
            item_type = str(item.get("Type") or item.get("type") or "").lower()
            if item_type and not any(token in item_type for token in ("common", "stock", "ordinary")):
                continue
            symbol = _to_yahoo_symbol(item.get("Code") or item.get("code"), exchange)
            if not symbol or len(symbol) > 24:
                continue
            universe.setdefault(symbol, {
                "symbol": symbol,
                "name": str(item.get("Name") or item.get("name") or symbol),
                "exchange": exchange,
                "region": meta["region"],
                "sector": str(item.get("Sector") or item.get("sector") or "Unknown"),
            })
    return list(universe.values())


def global_universe() -> list[dict[str, str]]:
    return cached_call("global_equity_universe", GLOBAL_UNIVERSE_TTL_SECONDS, _load_universe)


def _series(frame: pd.DataFrame, column: str) -> pd.Series:
    value = frame[column]
    if isinstance(value, pd.DataFrame):
        value = value.iloc[:, -1]
    return pd.to_numeric(value, errors="coerce").dropna()


def _classify_candidate(meta: dict[str, str], price: float, change_1d: float, relative_volume: float, avg_dollar_volume: float) -> tuple[str, str, bool]:
    sector = str(meta.get("sector") or "").lower()
    if "etf" in sector:
        category = "etf"
    elif price <= PENNY_STOCK_MAX_PRICE:
        category = "qualified_penny_stock"
    elif "mega_cap" in sector or meta.get("symbol") in CORE_STOCKS:
        category = "blue_chip_core"
    elif "large_cap" in sector:
        category = "large_cap"
    elif "mid_cap" in sector:
        category = "mid_cap"
    elif "small_cap" in sector:
        category = "small_cap"
    elif change_1d >= GLOBAL_MAJOR_MOVER_MIN_CHANGE_PCT:
        category = "major_gainer"
    elif change_1d <= -GLOBAL_MAJOR_MOVER_MIN_CHANGE_PCT:
        category = "major_loser"
    elif abs(change_1d) >= GLOBAL_GAP_MOVER_MIN_CHANGE_PCT:
        category = "gap_mover"
    elif relative_volume >= GLOBAL_UNUSUAL_VOLUME_MIN_RATIO:
        category = "unusual_volume"
    else:
        category = "dynamic_opportunity"
    if price <= PENNY_STOCK_MAX_PRICE:
        tradeable = price >= PENNY_STOCK_MIN_PRICE and avg_dollar_volume >= PENNY_STOCK_MIN_AVG_DOLLAR_VOLUME
        return category, "strict_penny_controls", tradeable
    return category, "standard", True


def _candidate_metrics(meta: dict[str, str]) -> GlobalCandidate | None:
    hist = get_history(meta["symbol"], "1mo", "1d")
    if hist is None or hist.empty or len(hist) < 6:
        return None
    close = _series(hist, "Close")
    if len(close) < 6:
        return None
    price = float(close.iloc[-1])
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
    change_1d = ((price / float(close.iloc[-2])) - 1.0) * 100.0 if close.iloc[-2] else 0.0
    change_5d = ((price / float(close.iloc[-6])) - 1.0) * 100.0 if close.iloc[-6] else 0.0
    recent_avg_volume = float(volume.iloc[-6:-1].mean()) if len(volume) >= 6 else avg_volume
    relative_volume = float(volume.iloc[-1] / recent_avg_volume) if recent_avg_volume > 0 else 1.0
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
    category, risk_bucket, tradeable = _classify_candidate(meta, price, change_1d, relative_volume, avg_dollar_volume)
    if not tradeable:
        return None
    return GlobalCandidate(
        symbol=meta["symbol"], name=meta.get("name", meta["symbol"]),
        exchange=meta.get("exchange", "Unknown"), region=meta.get("region", "Unknown"),
        sector=meta.get("sector", "Unknown"), price=price, change_1d_pct=change_1d,
        change_5d_pct=change_5d, relative_volume=relative_volume,
        avg_dollar_volume=avg_dollar_volume, volatility_pct=volatility,
        mover_score=round(mover_score, 3), category=category,
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
    # Always include core stocks/ETFs and regional anchors, then rotate through the full universe.
    seed_meta = [x for x in universe if x.get("exchange") == "SEED"][:12]
    core_meta = [x for x in universe if x.get("symbol") in CORE_STOCKS][:GLOBAL_CORE_SYMBOLS_PER_CYCLE]
    etf_meta = [x for x in universe if x.get("symbol") in ETF_SEEDS][:GLOBAL_ETF_SYMBOLS_PER_CYCLE]
    rotating_count = max(1, GLOBAL_SCAN_SYMBOLS_PER_CYCLE - len(seed_meta) - len(core_meta) - len(etf_meta))
    rotating = [universe[(cursor + i) % len(universe)] for i in range(rotating_count)]
    batch = list({x["symbol"]: x for x in seed_meta + core_meta + etf_meta + rotating}.values())
    found: list[GlobalCandidate] = []
    for meta in batch:
        try:
            candidate = _candidate_metrics(meta)
            if candidate:
                found.append(candidate)
        except Exception as exc:
            log.debug("Global candidate failed for %s: %s", meta.get("symbol"), exc)
    with connect() as conn:
        for candidate in found:
            payload = candidate.to_dict()
            conn.execute("""INSERT INTO global_market_candidates
                (symbol,name,exchange,region,sector,price,change_1d_pct,change_5d_pct,
                 relative_volume,avg_dollar_volume,volatility_pct,mover_score,category,risk_bucket,tradeable,payload,scanned_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                ON CONFLICT (symbol) DO UPDATE SET name=EXCLUDED.name,exchange=EXCLUDED.exchange,
                region=EXCLUDED.region,sector=EXCLUDED.sector,price=EXCLUDED.price,
                change_1d_pct=EXCLUDED.change_1d_pct,change_5d_pct=EXCLUDED.change_5d_pct,
                relative_volume=EXCLUDED.relative_volume,avg_dollar_volume=EXCLUDED.avg_dollar_volume,
                volatility_pct=EXCLUDED.volatility_pct,mover_score=EXCLUDED.mover_score,
                category=EXCLUDED.category,risk_bucket=EXCLUDED.risk_bucket,tradeable=EXCLUDED.tradeable,
                payload=EXCLUDED.payload,scanned_at=EXCLUDED.scanned_at""",
                (candidate.symbol,candidate.name,candidate.exchange,candidate.region,candidate.sector,
                 candidate.price,candidate.change_1d_pct,candidate.change_5d_pct,candidate.relative_volume,
                 candidate.avg_dollar_volume,candidate.volatility_pct,candidate.mover_score,
                 candidate.category,candidate.risk_bucket,candidate.tradeable,json.dumps(payload),candidate.scanned_at))
        next_cursor = (cursor + rotating_count) % len(universe)
        conn.execute("""INSERT INTO global_scanner_status
            (id,cursor,universe_size,scanned_count,active_count,status,message,updated_at)
            VALUES (1,%s,%s,%s,%s,'healthy',%s,%s)
            ON CONFLICT (id) DO UPDATE SET cursor=EXCLUDED.cursor,universe_size=EXCLUDED.universe_size,
            scanned_count=EXCLUDED.scanned_count,active_count=EXCLUDED.active_count,
            status=EXCLUDED.status,message=EXCLUDED.message,updated_at=EXCLUDED.updated_at""",
            (next_cursor,len(universe),len(batch),len(found),
             f"Scanned {len(batch)} worldwide symbols; {len(found)} passed liquidity and data checks.",utc_now()))
        rows = conn.execute("""SELECT symbol,name,exchange,region,sector,price,change_1d_pct,
            change_5d_pct,relative_volume,avg_dollar_volume,volatility_pct,mover_score,
            category,risk_bucket,tradeable,scanned_at
            FROM global_market_candidates ORDER BY mover_score DESC, scanned_at DESC LIMIT %s""",
            (GLOBAL_ACTIVE_CANDIDATES,)).fetchall()
    return [dict(row) for row in rows]


def active_global_watchlist() -> dict[str, str]:
    try:
        candidates = scan_global_markets()
        return {str(x["symbol"]): str(x.get("name") or x["symbol"]) for x in candidates}
    except Exception as exc:
        log.exception("Worldwide scan failed: %s", exc)
        return {}
