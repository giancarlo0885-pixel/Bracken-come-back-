from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from collections import defaultdict
import time
from typing import Callable

import pandas as pd
import requests

from api_manager import get_api_settings
from cache import cached_call
from config import (
    API_CACHE_TTL_SECONDS,
    PROVIDER_RATE_LIMIT_COOLDOWN_SECONDS,
    REALTIME_CACHE_TTL_SECONDS,
    UNAVAILABLE_SYMBOL_COOLDOWN_SECONDS,
)

log = logging.getLogger("provider-router")
_provider_cooldowns: dict[str, float] = {}
_symbol_cooldowns: dict[str, float] = {}
_failure_summary: dict[tuple[str, str], set[str]] = defaultdict(set)
_last_failure_log = 0.0
FAILURE_LOG_INTERVAL_SECONDS = 300


@dataclass
class ProviderAttempt:
    provider: str
    ok: bool
    records: int = 0
    status: str = ""
    error: str = ""


@dataclass
class RoutedHistory:
    frame: pd.DataFrame
    provider: str
    attempts: list[ProviderAttempt]
    fetched_at: str

    def metadata(self) -> dict:
        return {
            "provider": self.provider,
            "attempts": [asdict(x) for x in self.attempts],
            "fetched_at": self.fetched_at,
            "records": int(len(self.frame)),
        }


def _normalise(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    out = frame.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.get_level_values(0)
    lower_to_original = {str(c).lower(): c for c in out.columns}
    mapping = {}
    for wanted in ("Open", "High", "Low", "Close", "Volume"):
        found = lower_to_original.get(wanted.lower())
        if found is not None:
            mapping[found] = wanted
    out = out.rename(columns=mapping)
    out = out.loc[:, ~out.columns.duplicated(keep="last")]
    keep = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in out.columns]
    if "Close" not in keep:
        return pd.DataFrame()
    out = out[keep].apply(pd.to_numeric, errors="coerce").dropna(subset=["Close"])
    out.index = pd.to_datetime(out.index, errors="coerce", utc=True)
    out = out[~out.index.isna()].sort_index()
    return out[~out.index.duplicated(keep="last")]


def _is_intraday(interval: str) -> bool:
    text = str(interval or "1d").lower().strip()
    return not text.endswith("d") and text not in {"1wk", "1mo", "3mo"}


def _cooldown_active(store: dict[str, float], key: str) -> bool:
    until = store.get(key, 0.0)
    if until <= time.time():
        store.pop(key, None)
        return False
    return True


def mark_symbol_unavailable(symbol: str, seconds: int = UNAVAILABLE_SYMBOL_COOLDOWN_SECONDS) -> None:
    symbol = str(symbol or "").upper().strip()
    if symbol:
        _symbol_cooldowns[symbol] = time.time() + max(1, int(seconds))


def symbol_is_unavailable(symbol: str) -> bool:
    return _cooldown_active(_symbol_cooldowns, str(symbol or "").upper().strip())


def _mark_provider_limited(provider: str) -> None:
    _provider_cooldowns[provider] = time.time() + PROVIDER_RATE_LIMIT_COOLDOWN_SECONDS


def _record_failure(provider: str, status: str, symbol: str) -> None:
    global _last_failure_log
    _failure_summary[(provider, status)].add(symbol)
    now = time.time()
    if now - _last_failure_log < FAILURE_LOG_INTERVAL_SECONDS:
        return
    _last_failure_log = now
    for (failed_provider, failed_status), symbols in list(_failure_summary.items()):
        if not symbols:
            continue
        cooldown_until = _provider_cooldowns.get(failed_provider)
        cooldown_text = (
            datetime.fromtimestamp(cooldown_until, timezone.utc).isoformat()
            if cooldown_until
            else "not active"
        )
        sample = sorted(symbols)[:5]
        log.info(
            "Provider route failures summary | provider=%s status=%s affected_symbols=%d cooldown_until=%s sample=%s",
            failed_provider,
            failed_status,
            len(symbols),
            cooldown_text,
            ",".join(sample),
        )
    _failure_summary.clear()


def _period_days(period: str) -> int:
    text = str(period or "1y").lower().strip()
    mapping = {
        "1d": 2,
        "5d": 7,
        "1mo": 35,
        "3mo": 100,
        "6mo": 190,
        "1y": 370,
        "2y": 740,
        "5y": 1830,
        "10y": 3660,
        "max": 7300,
    }
    return mapping.get(text, 370)


def _polygon_interval(interval: str) -> tuple[int, str]:
    text = str(interval or "1d").lower().strip()
    if text.endswith("m"):
        try:
            return max(1, int(text[:-1])), "minute"
        except ValueError:
            return 1, "minute"
    if text.endswith("h"):
        try:
            return max(1, int(text[:-1])), "hour"
        except ValueError:
            return 1, "hour"
    return 1, "day"


def _polygon(symbol: str, period: str, interval: str, key: str) -> pd.DataFrame:
    multiplier, timespan = _polygon_interval(interval)
    end = datetime.now(timezone.utc).date()
    days = _period_days(period)
    start = pd.Timestamp(end) - pd.Timedelta(days=days)
    url = f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/{multiplier}/{timespan}/{start.date()}/{end}"
    response = requests.get(
        url,
        params={"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": key},
        timeout=12,
    )
    response.raise_for_status()
    records = response.json().get("results", [])
    if not records:
        return pd.DataFrame()
    frame = pd.DataFrame(records)
    frame.index = pd.to_datetime(frame["t"], unit="ms", utc=True)
    return _normalise(frame.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"}))


def _eodhd(symbol: str, period: str, interval: str, key: str) -> pd.DataFrame:
    if _is_intraday(interval):
        return pd.DataFrame()
    code = symbol if "." in symbol else f"{symbol}.US"
    start = (pd.Timestamp.utcnow() - pd.Timedelta(days=_period_days(period))).date()
    response = requests.get(
        f"https://eodhd.com/api/eod/{code}",
        params={"api_token": key, "fmt": "json", "from": str(start), "period": "d"},
        timeout=12,
    )
    response.raise_for_status()
    records = response.json()
    if not isinstance(records, list) or not records:
        return pd.DataFrame()
    frame = pd.DataFrame(records)
    frame.index = pd.to_datetime(frame["date"], utc=True)
    return _normalise(frame.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}))


def _alpha_interval(interval: str) -> str:
    text = str(interval or "5m").lower().strip()
    if text.endswith("h"):
        return "60min"
    if text.endswith("m"):
        try:
            value = int(text[:-1])
        except ValueError:
            value = 5
        allowed = (1, 5, 15, 30, 60)
        nearest = min(allowed, key=lambda candidate: abs(candidate - value))
        return f"{nearest}min"
    return "5min"


def _alpha(symbol: str, period: str, interval: str, key: str) -> pd.DataFrame:
    if _is_intraday(interval):
        alpha_interval = _alpha_interval(interval)
        response = requests.get(
            "https://www.alphavantage.co/query",
            params={
                "function": "TIME_SERIES_INTRADAY",
                "symbol": symbol,
                "interval": alpha_interval,
                "outputsize": "full" if _period_days(period) > 7 else "compact",
                "apikey": key,
            },
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        series = data.get(f"Time Series ({alpha_interval})", {})
        if not series:
            return pd.DataFrame()
        frame = pd.DataFrame.from_dict(series, orient="index")
        frame.index = pd.to_datetime(frame.index, utc=True)
        return _normalise(frame.rename(columns={"1. open": "Open", "2. high": "High", "3. low": "Low", "4. close": "Close", "5. volume": "Volume"}))

    response = requests.get(
        "https://www.alphavantage.co/query",
        params={"function": "TIME_SERIES_DAILY_ADJUSTED", "symbol": symbol, "outputsize": "full", "apikey": key},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    series = data.get("Time Series (Daily)", {})
    if not series:
        return pd.DataFrame()
    frame = pd.DataFrame.from_dict(series, orient="index")
    frame.index = pd.to_datetime(frame.index, utc=True)
    return _normalise(frame.rename(columns={"1. open": "Open", "2. high": "High", "3. low": "Low", "5. adjusted close": "Close", "6. volume": "Volume"}))


def _finnhub_resolution(interval: str) -> str:
    text = str(interval or "1d").lower().strip()
    if text.endswith("m"):
        try:
            return str(max(1, int(text[:-1])))
        except ValueError:
            return "5"
    if text.endswith("h"):
        try:
            return str(max(1, int(text[:-1])) * 60)
        except ValueError:
            return "60"
    return "D"


def _finnhub(symbol: str, period: str, interval: str, key: str) -> pd.DataFrame:
    now = int(pd.Timestamp.utcnow().timestamp())
    start = now - _period_days(period) * 86400
    response = requests.get(
        "https://finnhub.io/api/v1/stock/candle",
        params={"symbol": symbol, "resolution": _finnhub_resolution(interval), "from": start, "to": now, "token": key},
        timeout=12,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("s") != "ok":
        return pd.DataFrame()
    frame = pd.DataFrame(
        {
            "Open": data.get("o", []),
            "High": data.get("h", []),
            "Low": data.get("l", []),
            "Close": data.get("c", []),
            "Volume": data.get("v", []),
        },
        index=pd.to_datetime(data.get("t", []), unit="s", utc=True),
    )
    return _normalise(frame)


def route_history(
    symbol: str,
    period: str,
    interval: str,
    yahoo_loader: Callable[[str, str, str], pd.DataFrame],
) -> RoutedHistory:
    settings = get_api_settings()
    attempts: list[ProviderAttempt] = []
    intraday = _is_intraday(interval)
    ttl = REALTIME_CACHE_TTL_SECONDS if intraday else API_CACHE_TTL_SECONDS
    symbol = str(symbol or "").upper().strip()
    if symbol_is_unavailable(symbol):
        return RoutedHistory(
            pd.DataFrame(),
            "none",
            [ProviderAttempt("all", False, 0, "symbol_cooldown", "temporarily skipped after unavailable data")],
            datetime.now(timezone.utc).isoformat(),
        )

    if intraday:
        routes = [
            ("Polygon", "POLYGON_API_KEY", _polygon),
            ("Finnhub", "FINNHUB_API_KEY", _finnhub),
            ("Alpha Vantage", "ALPHA_VANTAGE_API_KEY", _alpha),
        ]
    else:
        routes = [
            ("Polygon", "POLYGON_API_KEY", _polygon),
            ("EODHD", "EODHD_API_KEY", _eodhd),
            ("Finnhub", "FINNHUB_API_KEY", _finnhub),
            ("Alpha Vantage", "ALPHA_VANTAGE_API_KEY", _alpha),
        ]

    for provider, key_name, function in routes:
        if _cooldown_active(_provider_cooldowns, provider):
            attempts.append(ProviderAttempt(provider, False, 0, "provider_cooldown"))
            continue
        key = settings.get(key_name)
        if not key:
            attempts.append(ProviderAttempt(provider, False, status="not_configured"))
            continue
        try:
            namespace = f"history_{provider.lower().replace(' ', '_')}_{interval}"
            frame = cached_call(namespace, ttl, function, symbol, period, interval, key)
            frame = _normalise(frame)
            if not frame.empty:
                attempts.append(ProviderAttempt(provider, True, len(frame), "healthy"))
                return RoutedHistory(frame, provider, attempts, datetime.now(timezone.utc).isoformat())
            attempts.append(ProviderAttempt(provider, False, 0, "no_data"))
        except Exception as exc:
            text = str(exc)[:220]
            status = "rate_limited" if "429" in text or "limit" in text.lower() else "degraded"
            if status == "rate_limited":
                _mark_provider_limited(provider)
            attempts.append(ProviderAttempt(provider, False, 0, status, text))
            _record_failure(provider, status, symbol)

    try:
        frame = _normalise(yahoo_loader(symbol, period, interval))
        if not frame.empty:
            attempts.append(ProviderAttempt("Yahoo Finance", True, len(frame), "fallback"))
            return RoutedHistory(frame, "Yahoo Finance", attempts, datetime.now(timezone.utc).isoformat())
    except Exception as exc:
        attempts.append(ProviderAttempt("Yahoo Finance", False, 0, "degraded", str(exc)[:220]))
        _record_failure("Yahoo Finance", "degraded", symbol)

    mark_symbol_unavailable(symbol)
    return RoutedHistory(pd.DataFrame(), "none", attempts, datetime.now(timezone.utc).isoformat())
