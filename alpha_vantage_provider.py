from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import threading
import time
from typing import Any

import pandas as pd
import requests

from api_manager import resolve_api_key
from cache import cached_call
from config import (
    ALPHA_VANTAGE_CACHE_TTL_SECONDS,
    ALPHA_VANTAGE_FUNDAMENTALS_TTL_SECONDS,
    ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS,
    ALPHA_VANTAGE_RATE_LIMIT_COOLDOWN_SECONDS,
)
from provider_router import normalize_symbol


ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
RATE_LIMIT_TERMS = (
    "standard api call frequency",
    "our standard api rate limit",
    "thank you for using alpha vantage",
    "api call frequency",
    "rate limit",
)
ERROR_TERMS = ("invalid api call", "invalid api key", "apikey", "error message")
FUNDAMENTAL_FUNCTIONS = {
    "OVERVIEW",
    "INCOME_STATEMENT",
    "BALANCE_SHEET",
    "CASH_FLOW",
    "EARNINGS",
    "ETF_PROFILE",
}


@dataclass
class AlphaHealth:
    provider: str = "Alpha Vantage"
    status: str = "not_configured"
    last_success: str | None = None
    last_error: str | None = None
    cooldown: str | None = None
    requests: int = 0
    mode: str = "Historical / EOD / Delayed"


_lock = threading.RLock()
_last_request_at = 0.0
_cooldown_until = 0.0
_last_success: str | None = None
_last_error: str | None = None
_request_count = 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _api_key() -> str:
    return resolve_api_key("ALPHA_VANTAGE_API_KEY")


def _finite(value: Any) -> float | None:
    try:
        number = float(str(value).replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _payload_text(payload: Any) -> str:
    return str(payload or "").lower()


def _classify_payload(payload: dict[str, Any]) -> tuple[bool, str]:
    text = _payload_text(payload)
    if any(term in text for term in RATE_LIMIT_TERMS) or "note" in {str(k).lower() for k in payload}:
        return False, "rate_limited"
    if any(term in text for term in ERROR_TERMS) or "information" in {str(k).lower() for k in payload}:
        return False, "invalid_or_unavailable"
    return True, "healthy"


def _set_cooldown(error: str, seconds: int = ALPHA_VANTAGE_RATE_LIMIT_COOLDOWN_SECONDS) -> None:
    global _cooldown_until, _last_error
    with _lock:
        _cooldown_until = time.time() + max(1, int(seconds))
        _last_error = error[:240]


def cooldown_remaining_seconds() -> int:
    with _lock:
        return max(0, int(_cooldown_until - time.time()))


def reset_state_for_tests() -> None:
    global _last_request_at, _cooldown_until, _last_success, _last_error, _request_count
    with _lock:
        _last_request_at = 0.0
        _cooldown_until = 0.0
        _last_success = None
        _last_error = None
        _request_count = 0


def _request_uncached(function: str, **params: Any) -> dict[str, Any]:
    global _last_request_at, _last_success, _last_error, _request_count
    key = _api_key()
    if not key:
        raise RuntimeError("Alpha Vantage API key is not configured")
    with _lock:
        remaining = _cooldown_until - time.time()
        if remaining > 0:
            raise RuntimeError(f"Alpha Vantage capability cooldown active for {int(remaining)} seconds")
        wait = ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS - (time.time() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    request_params = {"function": function, **params, "apikey": key}
    response = requests.get(ALPHA_VANTAGE_URL, params=request_params, timeout=20)
    with _lock:
        _last_request_at = time.time()
        _request_count += 1
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        payload = {}
    ok, status = _classify_payload(payload)
    if not ok:
        _set_cooldown(status)
        raise RuntimeError(f"Alpha Vantage {status}")
    with _lock:
        _last_success = _now_iso()
        _last_error = None
    return payload


def request(function: str, ttl_seconds: int = ALPHA_VANTAGE_CACHE_TTL_SECONDS, **params: Any) -> dict[str, Any]:
    namespace = f"alpha_vantage_{function}"
    return cached_call(namespace, ttl_seconds, _request_uncached, function, **params)


def health_probe() -> AlphaHealth:
    key = _api_key()
    with _lock:
        requests_count = _request_count
        last_success = _last_success
        last_error = _last_error
    if not key:
        return AlphaHealth(status="not_configured", requests=requests_count)
    cooldown = cooldown_remaining_seconds()
    if cooldown:
        return AlphaHealth(status="cooldown", last_success=last_success, last_error=last_error, cooldown=f"{cooldown}s", requests=requests_count)
    try:
        global_quote("IBM")
        return AlphaHealth(status="connected", last_success=_last_success, requests=_request_count, mode="Historical / EOD / Delayed")
    except Exception as exc:
        status = "cooldown" if cooldown_remaining_seconds() else "error"
        return AlphaHealth(status=status, last_success=last_success, last_error=str(exc)[:220], cooldown=f"{cooldown_remaining_seconds()}s" if cooldown_remaining_seconds() else None, requests=_request_count)


def global_quote(symbol: str) -> dict[str, Any] | None:
    requested = normalize_symbol(symbol)
    payload = request("GLOBAL_QUOTE", symbol=requested)
    quote = payload.get("Global Quote") if isinstance(payload, dict) else {}
    if not isinstance(quote, dict):
        return None
    provider_symbol = normalize_symbol(quote.get("01. symbol"))
    if provider_symbol != requested:
        return None
    price = _finite(quote.get("05. price"))
    if price is None or price <= 0:
        return None
    previous = _finite(quote.get("08. previous close"))
    change_pct = _finite(quote.get("10. change percent"))
    volume = _finite(quote.get("06. volume")) or 0.0
    latest_day = str(quote.get("07. latest trading day") or "").strip()
    return {
        "provider": "Alpha Vantage",
        "requested_symbol": requested,
        "provider_symbol": provider_symbol,
        "price": price,
        "previous_close": previous,
        "change_pct": change_pct if change_pct is not None else (((price / previous) - 1.0) * 100 if previous else 0.0),
        "volume": volume,
        "latest_trading_day": latest_day,
        "quote_timestamp": latest_day,
        "quote_verified": False,
        "mode": "EOD / Delayed",
        "provider_fetched_at": _now_iso(),
    }


def symbol_search(keywords: str) -> list[dict[str, Any]]:
    payload = request("SYMBOL_SEARCH", keywords=str(keywords or "").strip())
    records = payload.get("bestMatches") if isinstance(payload, dict) else []
    out: list[dict[str, Any]] = []
    for item in records if isinstance(records, list) else []:
        symbol = normalize_symbol(item.get("1. symbol"))
        if symbol:
            out.append(
                {
                    "symbol": symbol,
                    "name": item.get("2. name"),
                    "type": item.get("3. type"),
                    "region": item.get("4. region"),
                    "market_open": item.get("5. marketOpen"),
                    "market_close": item.get("6. marketClose"),
                    "timezone": item.get("7. timezone"),
                    "currency": item.get("8. currency"),
                    "match_score": _finite(item.get("9. matchScore")),
                    "provider": "Alpha Vantage",
                }
            )
    return out


def daily_history(symbol: str, outputsize: str = "compact") -> pd.DataFrame:
    requested = normalize_symbol(symbol)
    payload = request("TIME_SERIES_DAILY", symbol=requested, outputsize=outputsize)
    metadata = payload.get("Meta Data", {}) if isinstance(payload, dict) else {}
    provider_symbol = normalize_symbol(metadata.get("2. Symbol"))
    if provider_symbol != requested:
        return pd.DataFrame()
    series = payload.get("Time Series (Daily)", {})
    if not isinstance(series, dict) or not series:
        return pd.DataFrame()
    frame = pd.DataFrame.from_dict(series, orient="index")
    frame.index = pd.to_datetime(frame.index, errors="coerce")
    frame = frame.rename(columns={"1. open": "Open", "2. high": "High", "3. low": "Low", "4. close": "Close", "5. volume": "Volume"})
    frame = frame[["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce").dropna(subset=["Close"]).sort_index()
    frame.attrs.update({"requested_symbol": requested, "provider_symbol": provider_symbol, "provider": "Alpha Vantage", "quote_verified": True, "interval": "1d"})
    return frame


def market_status() -> list[dict[str, Any]]:
    payload = request("MARKET_STATUS", ttl_seconds=max(60, ALPHA_VANTAGE_CACHE_TTL_SECONDS))
    markets = payload.get("markets") if isinstance(payload, dict) else []
    return [item for item in markets if isinstance(item, dict)]


def top_gainers_losers() -> list[dict[str, Any]]:
    payload = request("TOP_GAINERS_LOSERS", ttl_seconds=max(60, ALPHA_VANTAGE_CACHE_TTL_SECONDS))
    out: list[dict[str, Any]] = []
    for field, mover_type in (("top_gainers", "major_gainer"), ("top_losers", "major_loser"), ("most_actively_traded", "unusual_volume")):
        records = payload.get(field, []) if isinstance(payload, dict) else []
        for item in records if isinstance(records, list) else []:
            symbol = normalize_symbol(item.get("ticker"))
            if not symbol:
                continue
            out.append(
                {
                    "symbol": symbol,
                    "mover_type": mover_type,
                    "price": _finite(item.get("price")),
                    "change_pct": _finite(item.get("change_percentage")),
                    "volume": _finite(item.get("volume")),
                    "quote_verified": False,
                    "mode": "Delayed / EOD",
                    "provider": "Alpha Vantage",
                    "provider_fetched_at": _now_iso(),
                }
            )
    return out


def news_sentiment(symbols: list[str] | None = None, topics: str = "financial_markets") -> list[dict[str, Any]]:
    params: dict[str, Any] = {"topics": topics}
    if symbols:
        params["tickers"] = ",".join(normalize_symbol(symbol) for symbol in symbols if symbol)
    payload = request("NEWS_SENTIMENT", ttl_seconds=max(900, ALPHA_VANTAGE_CACHE_TTL_SECONDS), **params)
    feed = payload.get("feed") if isinstance(payload, dict) else []
    out: list[dict[str, Any]] = []
    for item in feed if isinstance(feed, list) else []:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        out.append(
            {
                "title": item.get("title"),
                "description": item.get("summary"),
                "source": item.get("source"),
                "published_at": item.get("time_published"),
                "url": item.get("url"),
                "sentiment": item.get("overall_sentiment_label"),
                "sentiment_score": _finite(item.get("overall_sentiment_score")),
                "provider": "Alpha Vantage",
            }
        )
    return out


def fundamentals(symbol: str, function: str) -> dict[str, Any]:
    requested = normalize_symbol(symbol)
    selected = str(function or "").upper()
    if selected not in FUNDAMENTAL_FUNCTIONS:
        raise ValueError("Unsupported Alpha Vantage fundamentals function")
    payload = request(selected, ttl_seconds=ALPHA_VANTAGE_FUNDAMENTALS_TTL_SECONDS, symbol=requested)
    if isinstance(payload, dict):
        payload = dict(payload)
        payload.setdefault("provider", "Alpha Vantage")
        payload.setdefault("requested_symbol", requested)
    return payload
