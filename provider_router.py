from __future__ import annotations

import logging
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from collections import defaultdict
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
import requests

from alpha_vantage_provider import daily_history as alpha_daily_history
from api_manager import get_api_settings
from cache import cached_call, get as cache_get, make_key as cache_make_key, set_value as cache_set_value
from config import (
    API_CACHE_TTL_SECONDS,
    ALPHA_VANTAGE_PREMIUM,
    FINNHUB_CRYPTO_EXCHANGES,
    PROVIDER_CAPABILITY_DAILY_BUDGETS,
    PROVIDER_DAILY_REQUEST_BUDGETS,
    PROVIDER_PERMISSION_COOLDOWN_SECONDS,
    PROVIDER_RATE_LIMIT_COOLDOWN_SECONDS,
    REALTIME_CACHE_TTL_SECONDS,
    UNAVAILABLE_SYMBOL_COOLDOWN_SECONDS,
)
from asset_routing import infer_asset_class, is_in_market_scope
from global_adaptive_engine import (
    mark_provider_cooldown_live,
    provider_cooldown_active_live,
    reserve_provider_budget_live,
)
from provider_capabilities import (
    capability_available,
    capability_priority_penalty,
    classify_plan_limited_status,
    disable_capability,
    record_capability_result,
)
from security import redact_url as _security_redact_url

log = logging.getLogger("provider-router")
_provider_cooldowns: dict[str, float] = {}
_symbol_cooldowns: dict[str, float] = {}
_failure_summary: dict[tuple[str, str], set[str]] = defaultdict(set)
_last_failure_log = 0.0
FAILURE_LOG_INTERVAL_SECONDS = 300
EODHD_US_EXCHANGES = {"US", "NYSE", "NASDAQ", "NYSE ARCA", "NYSEARCA", "AMEX", "BATS"}
EODHD_STOCK_TYPES = {"common stock", "common share", "stock", "preferred stock", "preferred share"}
EODHD_ETF_TYPES = {"etf", "fund", "mutual fund"}
EODHD_EQUITY_TYPES = EODHD_STOCK_TYPES | EODHD_ETF_TYPES
CRYPTO_QUOTES = ("USD", "USDT", "USDC")
HISTORY_ROUTE_STRENGTH = {
    ("Polygon", "crypto"): 10,
    ("EODHD", "crypto"): 25,
    ("Finnhub", "crypto"): 35,
    ("Polygon", "intraday_history"): 10,
    ("Finnhub", "intraday_history"): 25,
    ("Alpha Vantage", "intraday_history"): 60,
    ("Polygon", "us_history"): 10,
    ("EODHD", "us_history"): 20,
    ("Finnhub", "us_history"): 35,
    ("Alpha Vantage", "us_history"): 45,
}


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
            "requested_symbol": self.frame.attrs.get("requested_symbol"),
            "provider_symbol": self.frame.attrs.get("provider_symbol"),
            "provider_native_symbol": self.frame.attrs.get("provider_native_symbol"),
            "period": self.frame.attrs.get("period"),
            "interval": self.frame.attrs.get("interval"),
            "source_identity": self.frame.attrs.get("source_identity"),
            "cache_identity": self.frame.attrs.get("cache_identity"),
            "quote_timestamp": self.frame.attrs.get("quote_timestamp"),
            "quote_age_seconds": self.frame.attrs.get("quote_age_seconds"),
            "source_mode": self.frame.attrs.get("source_mode"),
            "quote_verified": self.frame.attrs.get("quote_verified") is True,
        }


SECRET_QUERY_KEYS = {"api_token", "apikey", "token", "key", "authorization"}


def normalize_symbol(value: object) -> str:
    return str(value or "").upper().strip()


def canonical_crypto_symbol(value: object) -> str:
    symbol = normalize_symbol(value).replace("/", "-")
    if not symbol:
        return ""
    if symbol.endswith(".CC"):
        symbol = symbol[:-3]
    if "-" in symbol:
        base, quote = symbol.split("-", 1)
        quote = "USD" if quote in {"USDT", "USDC"} else quote
        return f"{base}-{quote}" if base and quote else ""
    for quote in CRYPTO_QUOTES:
        if symbol.endswith(quote) and len(symbol) > len(quote):
            base = symbol[: -len(quote)]
            return f"{base}-USD" if quote in {"USD", "USDT", "USDC"} else f"{base}-{quote}"
    return symbol


def polygon_crypto_native_symbol(symbol: str) -> str | None:
    canonical = canonical_crypto_symbol(symbol)
    if "-" not in canonical:
        return None
    base, quote = canonical.split("-", 1)
    if quote != "USD":
        return None
    return f"X:{base}USD"


def eodhd_crypto_native_symbol(symbol: str) -> str | None:
    canonical = canonical_crypto_symbol(symbol)
    if "-" not in canonical:
        return None
    base, quote = canonical.split("-", 1)
    if quote != "USD":
        return None
    return f"{base}-USD.CC"


def native_crypto_to_canonical(provider: str, native_symbol: object) -> str:
    native = normalize_symbol(native_symbol)
    provider_key = str(provider or "").lower()
    if provider_key == "polygon" and native.startswith("X:"):
        return canonical_crypto_symbol(native[2:])
    if provider_key == "eodhd" and native.endswith(".CC"):
        return canonical_crypto_symbol(native)
    if provider_key == "finnhub" and ":" in native:
        pair = native.split(":", 1)[1]
        return canonical_crypto_symbol(pair)
    return canonical_crypto_symbol(native)


def _symbol_matches(requested: str, provider_symbol: object) -> bool:
    return normalize_symbol(requested) == normalize_symbol(provider_symbol)


def _redact_url(text: str) -> str:
    return _security_redact_url(text)


def _select_symbol_columns(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
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


def _stamp_frame(
    frame: pd.DataFrame,
    provider: str,
    requested_symbol: str,
    provider_symbol: str,
    period: str,
    interval: str,
    adjusted: bool,
    extended_hours: bool,
    quote_verified: bool = False,
    provider_native_symbol: str | None = None,
) -> pd.DataFrame:
    out = frame.copy(deep=True)
    out.attrs.clear()
    out.attrs.update(
        {
            "requested_symbol": normalize_symbol(requested_symbol),
            "provider_symbol": normalize_symbol(provider_symbol),
            "provider_native_symbol": normalize_symbol(provider_native_symbol or provider_symbol),
            "provider": provider,
            "period": str(period),
            "interval": str(interval),
            "adjusted": bool(adjusted),
            "extended_hours": bool(extended_hours),
            "quote_verified": bool(quote_verified),
        }
    )
    return out


def _daily_like(interval: str) -> bool:
    text = str(interval or "1d").lower().strip()
    return text.endswith("d") or text in {"1wk", "1mo", "3mo", "1y"}


def _normalise(frame: pd.DataFrame, symbol: str = "", interval: str = "1d") -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    original_attrs = dict(getattr(frame, "attrs", {}) or {})
    out = frame.copy(deep=True)
    if isinstance(out.columns, pd.MultiIndex):
        out = _select_symbol_columns(out, symbol)
        if out.empty:
            return pd.DataFrame()
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
    index = pd.to_datetime(out.index, errors="coerce")
    if getattr(index, "tz", None) is None and not _daily_like(interval):
        return pd.DataFrame()
    if getattr(index, "tz", None) is not None:
        index = index.tz_convert(timezone.utc)
    out.index = index
    out = out[~out.index.isna()].sort_index()
    out = out[~out.index.duplicated(keep="last")]
    out.attrs.update(original_attrs)
    return out


def _verified_history(
    frame: pd.DataFrame,
    provider: str,
    requested_symbol: str,
    provider_symbol: str,
    period: str,
    interval: str,
    adjusted: bool = True,
    extended_hours: bool = True,
    identity_verified: bool = False,
    provider_native_symbol: str | None = None,
) -> pd.DataFrame:
    if not requested_symbol or not _symbol_matches(requested_symbol, provider_symbol):
        return pd.DataFrame()
    normalized = _normalise(frame, requested_symbol, interval)
    if normalized.empty:
        return pd.DataFrame()
    return _stamp_frame(
        normalized,
        provider,
        requested_symbol,
        provider_symbol,
        period,
        interval,
        adjusted,
        extended_hours,
        identity_verified,
        provider_native_symbol,
    )


def _latest_positive_close(frame: pd.DataFrame) -> float | None:
    if frame is None or frame.empty or "Close" not in frame.columns:
        return None
    close_value = frame["Close"]
    if isinstance(close_value, pd.DataFrame):
        close_value = close_value.iloc[:, -1]
    close = pd.to_numeric(close_value, errors="coerce")
    close = close[close.map(lambda value: pd.notna(value) and float(value) > 0)]
    if close.empty:
        return None
    return float(close.iloc[-1])


def _strict_yahoo_history(frame: pd.DataFrame, symbol: str, period: str, interval: str) -> pd.DataFrame:
    if not is_in_market_scope(symbol):
        return pd.DataFrame()
    verified = _verified_history(
        frame,
        "Yahoo Finance",
        symbol,
        symbol,
        period,
        interval,
        identity_verified=True,
    )
    if verified.empty or _latest_positive_close(verified) is None:
        return pd.DataFrame()
    verified.attrs["quote_verified"] = False
    verified.attrs["source_mode"] = "strict_research_fallback"
    return verified


def verify_frame_symbol(frame: pd.DataFrame, requested_symbol: str) -> bool:
    if frame is None or frame.empty:
        return False
    requested = normalize_symbol(requested_symbol)
    if not requested:
        return False
    frame_requested = normalize_symbol(frame.attrs.get("requested_symbol"))
    provider_symbol = normalize_symbol(frame.attrs.get("provider_symbol"))
    return frame_requested == requested and provider_symbol == requested


def _is_intraday(interval: str) -> bool:
    text = str(interval or "1d").lower().strip()
    return not text.endswith("d") and text not in {"1wk", "1mo", "3mo"}


def _cooldown_active(store: dict[str, float], key: str) -> bool:
    until = store.get(key, 0.0)
    if until <= time.time():
        store.pop(key, None)
        return False
    return True


def _interval_family(interval: str) -> str:
    return "intraday" if _is_intraday(interval) else "daily"


def _symbol_cooldown_key(symbol: str, provider: str = "all", capability: str = "all", interval_family: str = "all") -> str:
    return "|".join(
        [
            normalize_symbol(symbol),
            str(provider or "all").lower(),
            str(capability or "all").lower(),
            str(interval_family or "all").lower(),
        ]
    )


def mark_symbol_unavailable(
    symbol: str,
    seconds: int = UNAVAILABLE_SYMBOL_COOLDOWN_SECONDS,
    *,
    provider: str = "all",
    capability: str = "all",
    interval_family: str = "all",
) -> None:
    symbol = str(symbol or "").upper().strip()
    if symbol:
        _symbol_cooldowns[_symbol_cooldown_key(symbol, provider, capability, interval_family)] = time.time() + max(1, int(seconds))


def symbol_is_unavailable(
    symbol: str,
    provider: str = "all",
    capability: str = "all",
    interval_family: str = "all",
) -> bool:
    normalized_symbol = normalize_symbol(symbol)
    if provider == "all" and capability == "all" and interval_family == "all":
        prefix = f"{normalized_symbol}|"
        return any(
            key.startswith(prefix) and _cooldown_active(_symbol_cooldowns, key)
            for key in list(_symbol_cooldowns)
        )
    keys = [
        _symbol_cooldown_key(symbol, provider, capability, interval_family),
        _symbol_cooldown_key(symbol, "all", capability, interval_family),
        _symbol_cooldown_key(symbol, provider, capability, "all"),
        _symbol_cooldown_key(symbol, "all", "all", "all"),
    ]
    return any(_cooldown_active(_symbol_cooldowns, key) for key in keys)


def _mark_provider_limited(provider: str) -> None:
    _provider_cooldowns[provider] = time.time() + PROVIDER_RATE_LIMIT_COOLDOWN_SECONDS


def _history_capability(symbol: str, interval: str) -> str:
    asset = infer_asset_class(symbol)
    if asset == "crypto":
        return "crypto"
    if asset == "international_equity":
        return "international_history"
    return "us_history"


def _provider_status_from_exception(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    text = str(exc)
    for code in (402, 403, 401, 429, 404, 500, 503):
        if str(code) in text:
            return code
    return None


def classify_provider_failure(exc: Exception | str, status_code: int | None = None) -> str:
    text = _redact_url(str(exc)).lower()
    code = status_code
    if code is None and isinstance(exc, Exception):
        code = _provider_status_from_exception(exc)
    if code == 429 or "429" in text or "rate limit" in text or "too many requests" in text:
        return "rate_limited"
    if code == 402 or "payment required" in text:
        return "payment_required"
    if code in {401, 403} and any(term in text for term in ("plan", "premium", "permission", "entitlement", "subscription")):
        return "plan_limited"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "symbol" in text and ("mismatch" in text or "not found" in text or "invalid" in text):
        return "symbol_mismatch"
    if "malformed" in text or "json" in text or "parse" in text:
        return "malformed_response"
    if code and code >= 500:
        return "outage"
    return "degraded"


def _provider_budget(provider: str) -> int:
    return int(PROVIDER_DAILY_REQUEST_BUDGETS.get(str(provider or "").lower(), 0) or 0)


def _capability_budget(provider: str, capability: str) -> int | None:
    return PROVIDER_CAPABILITY_DAILY_BUDGETS.get((str(provider or "").lower(), capability))


def provider_supports_capability(provider: str, capability: str) -> bool:
    budget = _capability_budget(provider, capability)
    return budget is not None and int(budget or 0) > 0


def _history_route_priority(provider: str, capability: str) -> int:
    return HISTORY_ROUTE_STRENGTH.get((provider, capability), 100) + capability_priority_penalty(provider, capability)


def _ranked_history_routes(
    routes: list[tuple[str, str, Callable[[str, str, str, str], pd.DataFrame]]],
    capability: str,
) -> list[tuple[str, str, Callable[[str, str, str, str], pd.DataFrame]]]:
    return sorted(routes, key=lambda route: (_history_route_priority(route[0], capability), route[0]))


def crypto_execution_provider_redundancy(settings: dict[str, str] | None = None) -> dict[str, Any]:
    settings = settings or get_api_settings()
    providers = []
    for provider, key_name in (
        ("Polygon", "POLYGON_API_KEY"),
        ("Finnhub", "FINNHUB_API_KEY"),
        ("EODHD", "EODHD_API_KEY"),
    ):
        if provider_supports_capability(provider, "crypto") and settings.get(key_name):
            providers.append(provider)
    if len(providers) >= 2:
        status = "READY"
    elif len(providers) == 1:
        status = "DEGRADED"
    else:
        status = "UNAVAILABLE"
    return {"status": status, "verified_provider_count": len(providers), "providers": providers}


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
    is_crypto = infer_asset_class(symbol) == "crypto"
    requested_symbol = canonical_crypto_symbol(symbol) if is_crypto else normalize_symbol(symbol)
    provider_native_symbol = polygon_crypto_native_symbol(requested_symbol) if is_crypto else requested_symbol
    if not provider_native_symbol:
        return pd.DataFrame()
    multiplier, timespan = _polygon_interval(interval)
    end = datetime.now(timezone.utc).date()
    days = _period_days(period)
    start = pd.Timestamp(end) - pd.Timedelta(days=days)
    url = f"https://api.polygon.io/v2/aggs/ticker/{provider_native_symbol}/range/{multiplier}/{timespan}/{start.date()}/{end}"
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
    return _verified_history(
        frame.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"}),
        "Polygon",
        requested_symbol,
        requested_symbol,
        period,
        interval,
        identity_verified=True,
        provider_native_symbol=provider_native_symbol,
    )


def _load_eodhd_exchange_symbols(exchange: str, key: str) -> list[dict[str, Any]]:
    response = requests.get(
        f"https://eodhd.com/api/exchange-symbol-list/{exchange}",
        params={"api_token": key, "fmt": "json"},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else []


def _eodhd_symbol_mapping(symbol: str, key: str) -> dict[str, Any] | None:
    requested = normalize_symbol(symbol)
    if not requested or "." in requested:
        return None
    requested_code = requested
    records = cached_call(
        "eodhd_exchange_symbols_US",
        API_CACHE_TTL_SECONDS * 12,
        _load_eodhd_exchange_symbols,
        "US",
        key,
    )
    for record in records:
        code = normalize_symbol(record.get("Code") or record.get("code") or record.get("symbol"))
        provider_code = normalize_symbol(record.get("ProviderCode") or record.get("provider_code"))
        exchange = normalize_symbol(record.get("Exchange") or record.get("exchange") or record.get("ExchangeCode"))
        instrument_type = str(record.get("Type") or record.get("type") or "").strip().lower()
        if not code or not provider_code or not exchange or not instrument_type:
            continue
        if code != requested_code:
            continue
        if provider_code != f"{requested_code}.US":
            return None
        if exchange not in EODHD_US_EXCHANGES:
            return None
        if instrument_type not in EODHD_EQUITY_TYPES:
            return None
        asset_class = infer_asset_class(requested_code)
        if instrument_type in EODHD_ETF_TYPES and asset_class != "etf":
            return None
        if instrument_type in EODHD_STOCK_TYPES and asset_class == "etf":
            return None
        return {
            "requested_symbol": requested_code,
            "provider_code": provider_code,
            "exchange": exchange,
            "instrument_type": instrument_type,
        }
    return None


def _eodhd(symbol: str, period: str, interval: str, key: str) -> pd.DataFrame:
    if _is_intraday(interval):
        return pd.DataFrame()
    is_crypto = infer_asset_class(symbol) == "crypto"
    if is_crypto:
        requested_symbol = canonical_crypto_symbol(symbol)
        code = eodhd_crypto_native_symbol(requested_symbol)
        if not code or native_crypto_to_canonical("EODHD", code) != requested_symbol:
            return pd.DataFrame()
    else:
        mapping = _eodhd_symbol_mapping(symbol, key)
        if not mapping:
            return pd.DataFrame()
        requested_symbol = mapping["requested_symbol"]
        code = mapping["provider_code"]
    start = (pd.Timestamp.now("UTC") - pd.Timedelta(days=_period_days(period))).date()
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
    frame.index = pd.to_datetime(frame["date"], errors="coerce")
    return _verified_history(
        frame.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}),
        "EODHD",
        requested_symbol,
        requested_symbol,
        period,
        interval,
        identity_verified=True,
        provider_native_symbol=code,
    )


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
    if not ALPHA_VANTAGE_PREMIUM:
        if _is_intraday(interval):
            return pd.DataFrame()
        frame = alpha_daily_history(symbol, outputsize="full" if _period_days(period) > 100 else "compact", key_override=key)
        return _verified_history(
            frame,
            "Alpha Vantage",
            symbol,
            symbol,
            period,
            interval,
            identity_verified=True,
        )
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
        metadata = data.get("Meta Data", {}) if isinstance(data, dict) else {}
        returned_symbol = metadata.get("2. Symbol") or metadata.get("Symbol") or metadata.get("symbol")
        if not _symbol_matches(symbol, returned_symbol):
            return pd.DataFrame()
        series = data.get(f"Time Series ({alpha_interval})", {})
        if not series:
            return pd.DataFrame()
        metadata = data.get("Meta Data", {}) if isinstance(data, dict) else {}
        provider_timezone = (
            metadata.get("6. Time Zone")
            or metadata.get("Time Zone")
            or metadata.get("timezone")
        )
        if not provider_timezone:
            return pd.DataFrame()
        try:
            zone = ZoneInfo(str(provider_timezone))
        except ZoneInfoNotFoundError:
            return pd.DataFrame()
        frame = pd.DataFrame.from_dict(series, orient="index")
        frame.index = pd.to_datetime(frame.index, errors="coerce").tz_localize(zone).tz_convert(timezone.utc)
        return _verified_history(
            frame.rename(columns={"1. open": "Open", "2. high": "High", "3. low": "Low", "4. close": "Close", "5. volume": "Volume"}),
            "Alpha Vantage",
            symbol,
            symbol,
            period,
            interval,
            identity_verified=True,
        )

    response = requests.get(
        "https://www.alphavantage.co/query",
        params={"function": "TIME_SERIES_DAILY_ADJUSTED", "symbol": symbol, "outputsize": "full", "apikey": key},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    metadata = data.get("Meta Data", {}) if isinstance(data, dict) else {}
    returned_symbol = metadata.get("2. Symbol") or metadata.get("Symbol") or metadata.get("symbol")
    if not _symbol_matches(symbol, returned_symbol):
        return pd.DataFrame()
    series = data.get("Time Series (Daily)", {})
    if not series:
        return pd.DataFrame()
    frame = pd.DataFrame.from_dict(series, orient="index")
    frame.index = pd.to_datetime(frame.index, errors="coerce")
    return _verified_history(
        frame.rename(columns={"1. open": "Open", "2. high": "High", "3. low": "Low", "5. adjusted close": "Close", "6. volume": "Volume"}),
        "Alpha Vantage",
        symbol,
        symbol,
        period,
        interval,
        identity_verified=True,
    )


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


def _load_finnhub_crypto_symbols(exchange: str, key: str) -> list[dict[str, Any]]:
    response = requests.get(
        "https://finnhub.io/api/v1/crypto/symbol",
        params={"exchange": exchange, "token": key},
        timeout=12,
    )
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else []


def _finnhub_crypto_symbol(symbol: str, key: str) -> str | None:
    requested = canonical_crypto_symbol(symbol)
    if "-" not in requested:
        return None
    for exchange in FINNHUB_CRYPTO_EXCHANGES:
        records = cached_call(
            f"finnhub_crypto_symbols_{exchange}",
            API_CACHE_TTL_SECONDS * 12,
            _load_finnhub_crypto_symbols,
            exchange,
            key,
        )
        for record in records:
            candidate = normalize_symbol(record.get("symbol") or record.get("displaySymbol"))
            if not candidate:
                continue
            native = candidate if ":" in candidate else f"{exchange}:{candidate.replace('/', '')}"
            if native_crypto_to_canonical("Finnhub", native) == requested:
                return native
    return None


def _finnhub(symbol: str, period: str, interval: str, key: str) -> pd.DataFrame:
    is_crypto = infer_asset_class(symbol) == "crypto"
    requested_symbol = canonical_crypto_symbol(symbol) if is_crypto else normalize_symbol(symbol)
    endpoint = "https://finnhub.io/api/v1/crypto/candle" if is_crypto else "https://finnhub.io/api/v1/stock/candle"
    provider_native_symbol = _finnhub_crypto_symbol(requested_symbol, key) if is_crypto else requested_symbol
    if not provider_native_symbol:
        return pd.DataFrame()
    now = int(pd.Timestamp.now("UTC").timestamp())
    start = now - _period_days(period) * 86400
    response = requests.get(
        endpoint,
        params={"symbol": provider_native_symbol, "resolution": _finnhub_resolution(interval), "from": start, "to": now, "token": key},
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
    return _verified_history(
        frame,
        "Finnhub",
        requested_symbol,
        requested_symbol,
        period,
        interval,
        identity_verified=True,
        provider_native_symbol=provider_native_symbol,
    )


def route_history(
    symbol: str,
    period: str,
    interval: str,
    yahoo_loader: Callable[[str, str, str], pd.DataFrame],
) -> RoutedHistory:
    settings = get_api_settings()
    attempts: list[ProviderAttempt] = []
    intraday = _is_intraday(interval)
    asset_class = infer_asset_class(symbol)
    capability = _history_capability(symbol, interval)
    ttl = REALTIME_CACHE_TTL_SECONDS if intraday else API_CACHE_TTL_SECONDS
    symbol = str(symbol or "").upper().strip()
    interval_family = _interval_family(interval)
    if not is_in_market_scope(symbol):
        return RoutedHistory(
            pd.DataFrame(),
            "none",
            [ProviderAttempt("scope", False, 0, "scope_rejected", "MARKET_SCOPE=US_CRYPTO allows only U.S.-listed stocks/ETFs and crypto")],
            datetime.now(timezone.utc).isoformat(),
        )
    if symbol_is_unavailable(symbol, "all", capability, interval_family):
        return RoutedHistory(
            pd.DataFrame(),
            "none",
            [ProviderAttempt("all", False, 0, "symbol_cooldown", "temporarily skipped after unavailable data")],
            datetime.now(timezone.utc).isoformat(),
        )

    if asset_class == "crypto":
        routes = [
            ("Polygon", "POLYGON_API_KEY", _polygon),
            ("Finnhub", "FINNHUB_API_KEY", _finnhub),
            ("EODHD", "EODHD_API_KEY", _eodhd),
        ]
        routes = [route for route in routes if provider_supports_capability(route[0], "crypto")]
    elif intraday:
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

    for provider, key_name, function in _ranked_history_routes(routes, capability):
        if not capability_available(provider, capability):
            attempts.append(ProviderAttempt(provider, False, 0, "capability_cooldown_or_unsupported"))
            continue
        shared_cooldown = provider_cooldown_active_live(provider)
        if shared_cooldown.get("active"):
            attempts.append(ProviderAttempt(provider, False, 0, "provider_shared_cooldown", str(shared_cooldown.get("reason") or "provider cooldown")))
            continue
        if _cooldown_active(_provider_cooldowns, provider):
            attempts.append(ProviderAttempt(provider, False, 0, "provider_cooldown"))
            continue
        if symbol_is_unavailable(symbol, provider, capability, interval_family):
            attempts.append(ProviderAttempt(provider, False, 0, "symbol_capability_cooldown", "temporarily skipped after scoped provider/capability failure"))
            continue
        key = settings.get(key_name)
        if not key:
            attempts.append(ProviderAttempt(provider, False, status="not_configured"))
            continue
        budget_key = (provider.lower(), capability)
        daily_budget = PROVIDER_CAPABILITY_DAILY_BUDGETS.get(budget_key)
        if daily_budget is None:
            attempts.append(ProviderAttempt(provider, False, 0, "provider_budget_unknown", "provider capability budget is not configured"))
            continue
        try:
            namespace = (
                f"history_{provider.lower().replace(' ', '_')}_{symbol}_{period}_{interval}"
                f"_adjusted_true_extended_{str(intraday).lower()}"
            )
            cache_key = cache_make_key(namespace, symbol, period, interval)
            cached_frame = cache_get(cache_key)
            if cached_frame is not None:
                frame = cached_frame
            else:
                provider_wide_budget = reserve_provider_budget_live(
                    provider,
                    "__provider__",
                    daily_budget=_provider_budget(provider),
                    entitlement="configured",
                    data_mode="provider-wide",
                )
                if not provider_wide_budget.get("reserved"):
                    attempts.append(ProviderAttempt(provider, False, 0, "provider_budget_blocked", str(provider_wide_budget.get("reason") or "provider budget unavailable")))
                    continue
                budget = reserve_provider_budget_live(provider, capability, daily_budget=daily_budget, entitlement="configured", data_mode="intraday" if intraday else "historical")
                if not budget.get("reserved"):
                    attempts.append(ProviderAttempt(provider, False, 0, "provider_budget_blocked", str(budget.get("reason") or "budget unavailable")))
                    continue
                frame = function(symbol, period, interval, key)
                cache_set_value(cache_key, frame, ttl)
            frame = _normalise(frame, symbol, interval)
            if not frame.empty:
                frame.attrs["cache_identity"] = namespace
                frame.attrs.setdefault("source_identity", f"{provider}:{symbol}:{period}:{interval}")
                frame.attrs["period"] = period
                frame.attrs["interval"] = interval
            if not frame.empty and frame.attrs.get("quote_verified") is True and verify_frame_symbol(frame, symbol):
                frame = frame.copy(deep=True)
                record_capability_result(provider, capability, True)
                attempts.append(ProviderAttempt(provider, True, len(frame), "healthy"))
                return RoutedHistory(frame, provider, attempts, datetime.now(timezone.utc).isoformat())
            attempts.append(ProviderAttempt(provider, False, 0, "symbol_mismatch_or_no_data"))
            record_capability_result(provider, capability, False, "symbol_mismatch_or_no_data")
            mark_symbol_unavailable(
                symbol,
                provider=provider,
                capability=capability,
                interval_family=interval_family,
            )
        except Exception as exc:
            text = _redact_url(str(exc))[:220]
            status_code = _provider_status_from_exception(exc)
            if classify_plan_limited_status(status_code, text):
                disable_capability(
                    provider,
                    capability,
                    text,
                    status_code=status_code,
                    seconds=PROVIDER_PERMISSION_COOLDOWN_SECONDS,
                )
                status = "capability_plan_limited"
            else:
                status = classify_provider_failure(exc, status_code)
            if status == "rate_limited":
                _mark_provider_limited(provider)
                mark_provider_cooldown_live(
                    provider,
                    seconds=PROVIDER_RATE_LIMIT_COOLDOWN_SECONDS,
                    reason=text or "provider rate limited",
                )
            record_capability_result(provider, capability, False, status, status_code=status_code)
            attempts.append(ProviderAttempt(provider, False, 0, status, text))
            _record_failure(provider, status, symbol)
            mark_symbol_unavailable(
                symbol,
                provider=provider,
                capability=capability,
                interval_family=interval_family,
            )

    try:
        frame = _strict_yahoo_history(yahoo_loader(symbol, period, interval), symbol, period, interval)
        if not frame.empty and verify_frame_symbol(frame, symbol):
            frame.attrs["source_identity"] = f"Yahoo Finance:{symbol}:{period}:{interval}"
            frame.attrs["period"] = period
            frame.attrs["interval"] = interval
            record_capability_result("Yahoo Finance", capability, True)
            attempts.append(ProviderAttempt("Yahoo Finance", True, len(frame), "strict_research_fallback"))
            return RoutedHistory(frame, "Yahoo Finance", attempts, datetime.now(timezone.utc).isoformat())
    except Exception as exc:
        attempts.append(ProviderAttempt("Yahoo Finance", False, 0, "degraded", _redact_url(str(exc))[:220]))
        record_capability_result("Yahoo Finance", capability, False, "degraded")
        _record_failure("Yahoo Finance", "degraded", symbol)

    mark_symbol_unavailable(symbol, provider="all", capability=capability, interval_family=interval_family)
    return RoutedHistory(pd.DataFrame(), "none", attempts, datetime.now(timezone.utc).isoformat())
