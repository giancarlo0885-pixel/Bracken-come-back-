from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
import threading
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd
import requests

from api_manager import resolve_api_key
from cache import cached_call
from config import (
    ALPHA_VANTAGE_CACHE_TTL_SECONDS,
    ALPHA_VANTAGE_DAILY_REQUEST_BUDGET,
    ALPHA_VANTAGE_FUNDAMENTALS_TTL_SECONDS,
    ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS,
    ALPHA_VANTAGE_PREMIUM,
    ALPHA_VANTAGE_RATE_LIMIT_COOLDOWN_SECONDS,
)


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
_memory_usage: dict[tuple[str, str], dict[str, Any]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _api_key() -> str:
    return resolve_api_key("ALPHA_VANTAGE_API_KEY")


def normalize_symbol(value: Any) -> str:
    return str(value or "").upper().strip()


def sanitize_error(value: Any) -> str:
    text = str(value or "").strip()
    if not text and isinstance(value, BaseException):
        text = value.__class__.__name__
    secret = _api_key()

    def redact_url(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            parts = urlsplit(raw)
            query = urlencode(
                [
                    (key, "REDACTED" if key.lower() in {"apikey", "api_key", "key", "token", "api_token"} else param_value)
                    for key, param_value in parse_qsl(parts.query, keep_blank_values=True)
                ]
            )
            return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
        except Exception:
            return raw

    text = re.sub(r"https?://[^\s)]+", redact_url, text)
    text = re.sub(r"(?i)(apikey=)[^&\s)]+", r"\1REDACTED", text)
    if secret:
        text = text.replace(secret, "REDACTED")
    return text[:240]


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
        _last_error = sanitize_error(error)


def cooldown_remaining_seconds() -> int:
    with _lock:
        return max(0, int(_cooldown_until - time.time()))


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _read_usage_row() -> dict[str, Any]:
    try:
        from database import connect

        with connect() as conn:
            return conn.execute(
                """
                SELECT provider, usage_date, requests_used, daily_budget, last_request_at, last_success, last_error
                FROM provider_daily_usage
                WHERE provider=%s AND usage_date=%s
                """,
                ("Alpha Vantage", _today()),
            ).fetchone() or {}
    except Exception:
        with _lock:
            return dict(_memory_usage.get(("Alpha Vantage", _today()), {}))


def usage_snapshot() -> dict[str, Any]:
    row = _read_usage_row()
    used = int(row.get("requests_used") or 0)
    budget = int(row.get("daily_budget") or ALPHA_VANTAGE_DAILY_REQUEST_BUDGET)
    return {
        "provider": "Alpha Vantage",
        "usage_date": _today(),
        "requests_used": used,
        "daily_budget": budget,
        "daily_remaining": max(0, budget - used),
        "last_request_at": row.get("last_request_at"),
        "last_success": row.get("last_success") or _last_success,
        "last_error": sanitize_error(row.get("last_error") or _last_error or ""),
    }


def _reserve_request() -> None:
    budget = int(ALPHA_VANTAGE_DAILY_REQUEST_BUDGET)
    if budget <= 0:
        _set_cooldown("quota_exhausted")
        raise RuntimeError("Alpha Vantage quota_exhausted")
    now_iso = _now_iso()
    def reserve_memory() -> None:
        with _lock:
            key = ("Alpha Vantage", _today())
            row = _memory_usage.setdefault(key, {"provider": "Alpha Vantage", "usage_date": _today(), "requests_used": 0, "daily_budget": budget})
            if int(row.get("requests_used") or 0) >= budget:
                row["last_error"] = "quota_exhausted"
                _set_cooldown("quota_exhausted")
                raise RuntimeError("Alpha Vantage quota_exhausted")
            row["requests_used"] = int(row.get("requests_used") or 0) + 1
            row["daily_budget"] = budget
            row["last_request_at"] = now_iso

    try:
        from database import connect

        with connect() as conn:
            row = conn.execute(
                """
                INSERT INTO provider_daily_usage
                (provider, usage_date, requests_used, daily_budget, last_request_at)
                VALUES (%s,%s,0,%s,NULL)
                ON CONFLICT (provider, usage_date) DO UPDATE SET
                    daily_budget=EXCLUDED.daily_budget
                RETURNING requests_used, daily_budget
                """,
                ("Alpha Vantage", _today(), budget),
            ).fetchone() or {}
            used = int(row.get("requests_used") or 0)
            current_budget = int(row.get("daily_budget") or budget)
            if used >= current_budget:
                conn.execute(
                    """
                    UPDATE provider_daily_usage
                    SET last_error=%s
                    WHERE provider=%s AND usage_date=%s
                    """,
                    ("quota_exhausted", "Alpha Vantage", _today()),
                )
                _set_cooldown("quota_exhausted")
                raise RuntimeError("Alpha Vantage quota_exhausted")
            conn.execute(
                """
                UPDATE provider_daily_usage
                SET requests_used=requests_used+1, daily_budget=%s, last_request_at=%s
                WHERE provider=%s AND usage_date=%s
                """,
                (budget, now_iso, "Alpha Vantage", _today()),
            )
            return
    except RuntimeError as exc:
        if "quota_exhausted" in str(exc):
            raise
        reserve_memory()
    except Exception:
        reserve_memory()


def _persist_result(*, success: bool, error: str = "") -> None:
    safe_error = sanitize_error(error)
    success_time = _now_iso() if success else None
    try:
        from database import connect

        with connect() as conn:
            conn.execute(
                """
                INSERT INTO provider_daily_usage
                (provider, usage_date, requests_used, daily_budget, last_request_at, last_success, last_error)
                VALUES (%s,%s,0,%s,%s,%s,%s)
                ON CONFLICT (provider, usage_date) DO UPDATE SET
                    daily_budget=EXCLUDED.daily_budget,
                    last_success=COALESCE(EXCLUDED.last_success, provider_daily_usage.last_success),
                    last_error=EXCLUDED.last_error
                """,
                ("Alpha Vantage", _today(), ALPHA_VANTAGE_DAILY_REQUEST_BUDGET, _now_iso(), success_time, safe_error),
            )
    except Exception:
        with _lock:
            key = ("Alpha Vantage", _today())
            row = _memory_usage.setdefault(key, {"provider": "Alpha Vantage", "usage_date": _today(), "requests_used": 0, "daily_budget": ALPHA_VANTAGE_DAILY_REQUEST_BUDGET})
            if success_time:
                row["last_success"] = success_time
            row["last_error"] = safe_error


def reset_state_for_tests() -> None:
    global _last_request_at, _cooldown_until, _last_success, _last_error, _request_count
    with _lock:
        _last_request_at = 0.0
        _cooldown_until = 0.0
        _last_success = None
        _last_error = None
        _request_count = 0
        _memory_usage.clear()


def _request_uncached(function: str, _key_override: str | None = None, **params: Any) -> dict[str, Any]:
    global _last_request_at, _last_success, _last_error, _request_count
    key = _key_override or _api_key()
    if not key:
        raise RuntimeError("Alpha Vantage API key is not configured")
    with _lock:
        remaining = _cooldown_until - time.time()
        if remaining > 0:
            raise RuntimeError(f"Alpha Vantage capability cooldown active for {int(remaining)} seconds")
        wait = ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS - (time.time() - _last_request_at)
    _reserve_request()
    if wait > 0:
        time.sleep(wait)
    request_params = {"function": function, **params, "apikey": key}
    try:
        response = requests.get(ALPHA_VANTAGE_URL, params=request_params, timeout=20)
        with _lock:
            _last_request_at = time.time()
            _request_count += 1
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        safe = sanitize_error(exc)
        _persist_result(success=False, error=safe)
        with _lock:
            _last_error = safe
        raise RuntimeError(f"Alpha Vantage request failed: {safe}") from None
    if not isinstance(payload, dict):
        payload = {}
    ok, status = _classify_payload(payload)
    if not ok:
        _set_cooldown(status)
        _persist_result(success=False, error=status)
        raise RuntimeError(f"Alpha Vantage {status}")
    with _lock:
        _last_success = _now_iso()
        _last_error = None
    _persist_result(success=True)
    return payload


def request(function: str, ttl_seconds: int = ALPHA_VANTAGE_CACHE_TTL_SECONDS, **params: Any) -> dict[str, Any]:
    namespace = f"alpha_vantage_{function}"
    return cached_call(namespace, ttl_seconds, _request_uncached, function, **params)


def health_probe(probe: bool = False) -> AlphaHealth:
    key = _api_key()
    usage = usage_snapshot()
    with _lock:
        requests_count = _request_count
        last_success = _last_success
        last_error = _last_error
    if not key:
        return AlphaHealth(status="not_configured", requests=requests_count)
    cooldown = cooldown_remaining_seconds()
    if cooldown:
        return AlphaHealth(status="cooldown", last_success=usage.get("last_success") or last_success, last_error=sanitize_error(usage.get("last_error") or last_error or ""), cooldown=f"{cooldown}s", requests=int(usage.get("requests_used") or requests_count))
    if int(usage.get("daily_remaining") or 0) <= 0:
        return AlphaHealth(status="quota_exhausted", last_success=usage.get("last_success") or last_success, last_error=sanitize_error(usage.get("last_error") or "quota_exhausted"), cooldown="daily budget exhausted", requests=int(usage.get("requests_used") or requests_count))
    if not probe:
        status = "connected" if usage.get("last_success") else "configured"
        return AlphaHealth(status=status, last_success=usage.get("last_success") or last_success, last_error=sanitize_error(usage.get("last_error") or last_error or ""), requests=int(usage.get("requests_used") or requests_count), mode="Historical / EOD / Delayed")
    try:
        global_quote("IBM")
        return AlphaHealth(status="connected", last_success=_last_success, requests=usage_snapshot()["requests_used"], mode="Historical / EOD / Delayed")
    except Exception as exc:
        status = "cooldown" if cooldown_remaining_seconds() else "error"
        safe = sanitize_error(exc)
        return AlphaHealth(status=status, last_success=last_success, last_error=safe, cooldown=f"{cooldown_remaining_seconds()}s" if cooldown_remaining_seconds() else None, requests=usage_snapshot()["requests_used"])


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


def daily_history(symbol: str, outputsize: str = "compact", key_override: str | None = None) -> pd.DataFrame:
    requested = normalize_symbol(symbol)
    payload = _request_uncached("TIME_SERIES_DAILY", _key_override=key_override, symbol=requested, outputsize=outputsize) if key_override else request("TIME_SERIES_DAILY", symbol=requested, outputsize=outputsize)
    metadata = payload.get("Meta Data", {}) if isinstance(payload, dict) else {}
    provider_symbol = normalize_symbol(metadata.get("2. Symbol"))
    if provider_symbol != requested:
        return pd.DataFrame()
    series = payload.get("Time Series (Daily)", {})
    if not isinstance(series, dict) or not series:
        return pd.DataFrame()
    frame = pd.DataFrame.from_dict(series, orient="index")
    frame.index = pd.to_datetime(frame.index, errors="coerce")
    if "4. close" not in frame.columns and "5. adjusted close" in frame.columns:
        frame["4. close"] = frame["5. adjusted close"]
    if "5. volume" not in frame.columns and "6. volume" in frame.columns:
        frame["5. volume"] = frame["6. volume"]
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
