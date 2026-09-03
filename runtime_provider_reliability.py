from __future__ import annotations

import logging
import math
import os
from pathlib import Path
import threading
import time
from typing import Any

import pandas as pd

from config import UNAVAILABLE_SYMBOL_COOLDOWN_SECONDS


log = logging.getLogger("runtime-provider-reliability")
_LOCK = threading.Lock()
_KEY_LOCKS: dict[str, threading.Lock] = {}
_UNAVAILABLE_UNTIL: dict[str, float] = {}
_INSTALLED = False

# Yahoo disambiguates several crypto tickers with CoinMarketCap-style numeric
# suffixes. Keep Oracle symbols canonical and translate only at the provider
# boundary so execution, Coinbase consensus, and portfolio identity never inherit
# Yahoo's provider-native naming convention.
_YAHOO_CRYPTO_NATIVE_SYMBOLS = {
    "APT-USD": "APT21794-USD",
    "ARB-USD": "ARB11841-USD",
    "PEPE-USD": "PEPE24478-USD",
    "POL-USD": "POL28321-USD",
    "SUI-USD": "SUI20947-USD",
    "UNI-USD": "UNI7083-USD",
}


def yahoo_native_symbol(symbol: str) -> str:
    requested = str(symbol or "").upper().strip()
    return _YAHOO_CRYPTO_NATIVE_SYMBOLS.get(requested, requested)


def _install_yfinance_cache(market_data_module: Any) -> str:
    """Use one explicit writable timezone-cache directory per container.

    yfinance's default ``/root/.cache/py-yfinance`` initialization can race when
    the always-on worker launches fast and deep scans concurrently. Configure the
    cache once before any Ticker object is created so that harmless directory
    creation races do not disable the cache or pollute production logs.
    """
    location = str(os.getenv("YFINANCE_TZ_CACHE_DIR", "/tmp/oracle-yfinance-tz") or "/tmp/oracle-yfinance-tz").strip()
    try:
        Path(location).mkdir(parents=True, exist_ok=True)
        setter = getattr(market_data_module.yf, "set_tz_cache_location", None)
        if callable(setter):
            setter(location)
            return location
    except Exception as exc:
        log.info("YFINANCE CACHE SETUP | status=FALLBACK | reason=%s", exc.__class__.__name__)
    return "default"


def _install_current_crypto_universe() -> bool:
    """Retire the legacy MATIC seed in favor of Polygon's active POL token.

    The watchlist object is imported by reference throughout the runtime. Mutating
    it in place updates the scanner/opportunity-engine seed without touching
    historical database rows or held positions. Held legacy symbols are still
    reintroduced by the worker's held-position path for risk monitoring/exits.
    """
    import config

    watchlist = getattr(config, "CRYPTO_WATCHLIST", None)
    if not isinstance(watchlist, dict):
        return False

    changed = False
    if "MATIC-USD" in watchlist:
        watchlist.pop("MATIC-USD", None)
        changed = True
    if "POL-USD" not in watchlist:
        watchlist["POL-USD"] = "Polygon Ecosystem Token"
        changed = True

    watchlists = getattr(config, "WATCHLISTS", None)
    if isinstance(watchlists, dict):
        watchlists["crypto"] = watchlist
    return changed


def _key(symbol: str, period: str, interval: str) -> str:
    return "|".join(
        [
            str(symbol or "").upper().strip(),
            str(period or "").lower().strip(),
            str(interval or "").lower().strip(),
        ]
    )


def _key_lock(key: str) -> threading.Lock:
    with _LOCK:
        lock = _KEY_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _KEY_LOCKS[key] = lock
        return lock


def _cooldown_active(key: str) -> bool:
    now = time.monotonic()
    with _LOCK:
        until = float(_UNAVAILABLE_UNTIL.get(key) or 0.0)
        if until <= now:
            _UNAVAILABLE_UNTIL.pop(key, None)
            return False
        return True


def _mark_unavailable(key: str) -> None:
    try:
        seconds = max(60, int(UNAVAILABLE_SYMBOL_COOLDOWN_SECONDS))
    except (TypeError, ValueError):
        seconds = 900
    with _LOCK:
        _UNAVAILABLE_UNTIL[key] = time.monotonic() + seconds


def _clear_unavailable(key: str) -> None:
    with _LOCK:
        _UNAVAILABLE_UNTIL.pop(key, None)


def yahoo_reference_history(market_data_module: Any, symbol: str, period: str, interval: str) -> pd.DataFrame:
    """Fetch a Yahoo research/paper reference without duplicate failure storms.

    yfinance's bulk ``download`` helper logs missing symbols at ERROR before the
    caller can classify the result. ``Ticker.history(..., raise_errors=True)``
    instead raises the provider exception to us. We convert it to an empty frame
    and place that exact canonical symbol/period/interval on a cooldown. A per-key
    lock prevents concurrent fast/deep scans from issuing the same doomed request.

    Yahoo provider-native aliases are resolved only at this boundary. The returned
    frame always preserves the Oracle canonical requested/provider identity and
    exposes the Yahoo-native instrument separately as ``provider_native_symbol``.

    This function does not make Yahoo provider-verified. Existing market_data
    logic remains responsible for exact identity/freshness checks and the crypto
    execution guard still requires Coinbase consensus for Yahoo paper marks.
    """
    requested = str(symbol or "").upper().strip()
    provider_native = yahoo_native_symbol(requested)
    key = _key(requested, period, interval)
    if not requested or _cooldown_active(key):
        return pd.DataFrame()

    lock = _key_lock(key)
    with lock:
        if _cooldown_active(key):
            return pd.DataFrame()
        try:
            ticker = market_data_module.yf.Ticker(provider_native)
            data = ticker.history(
                period=period,
                interval=interval,
                auto_adjust=True,
                prepost=True,
                raise_errors=True,
            )
        except Exception as exc:
            _mark_unavailable(key)
            log.info(
                "YAHOO REFERENCE UNAVAILABLE | symbol=%s | provider_native_symbol=%s | period=%s | interval=%s | reason=%s | cooldown=active",
                requested,
                provider_native,
                period,
                interval,
                exc.__class__.__name__,
            )
            return pd.DataFrame()

        normalized = market_data_module._normalize(data, provider_native)
        if normalized is None or normalized.empty:
            _mark_unavailable(key)
            log.info(
                "YAHOO REFERENCE UNAVAILABLE | symbol=%s | provider_native_symbol=%s | period=%s | interval=%s | reason=EMPTY_HISTORY | cooldown=active",
                requested,
                provider_native,
                period,
                interval,
            )
            return pd.DataFrame()

        try:
            close = pd.to_numeric(normalized["Close"], errors="coerce").dropna()
            latest = float(close.iloc[-1]) if len(close) else float("nan")
        except Exception:
            latest = float("nan")
        if not math.isfinite(latest) or latest <= 0:
            _mark_unavailable(key)
            return pd.DataFrame()

        _clear_unavailable(key)
        stamped = market_data_module._stamp_history(
            normalized,
            requested,
            requested,
            "Yahoo Finance",
            interval,
        )
        stamped.attrs["provider_native_symbol"] = provider_native
        return stamped


def _install_yahoo_strict_alias_preservation() -> None:
    import provider_router

    original = provider_router._strict_yahoo_history
    if getattr(original, "_oracle_yahoo_alias_aware", False):
        return

    def alias_aware(frame: pd.DataFrame, symbol: str, period: str, interval: str) -> pd.DataFrame:
        requested = str(symbol or "").upper().strip()
        provider_native = str(
            getattr(frame, "attrs", {}).get("provider_native_symbol")
            or yahoo_native_symbol(requested)
        ).upper().strip()
        if provider_native == requested:
            return original(frame, requested, period, interval)
        if not provider_router.is_in_market_scope(requested):
            return pd.DataFrame()

        normalized = provider_router._normalise(frame, provider_native, interval)
        if normalized.empty or provider_router._latest_positive_close(normalized) is None:
            return pd.DataFrame()
        verified = provider_router._stamp_frame(
            normalized,
            "Yahoo Finance",
            requested,
            requested,
            period,
            interval,
            True,
            True,
            False,
            provider_native,
        )
        verified.attrs["quote_verified"] = False
        verified.attrs["source_mode"] = "strict_research_fallback"
        return verified

    alias_aware._oracle_yahoo_alias_aware = True
    provider_router._strict_yahoo_history = alias_aware


def install_yahoo_runtime_reliability() -> None:
    """Install current crypto symbols plus the alias-aware Yahoo fallback."""
    global _INSTALLED
    if _INSTALLED:
        return
    import market_data

    cache_location = _install_yfinance_cache(market_data)
    universe_changed = _install_current_crypto_universe()

    def patched(symbol: str, period: str, interval: str) -> pd.DataFrame:
        return yahoo_reference_history(market_data, symbol, period, interval)

    market_data._download_yahoo = patched
    _install_yahoo_strict_alias_preservation()
    _INSTALLED = True
    log.info(
        "Installed Yahoo fallback single-flight, native-symbol aliases, unavailable-symbol cooldown, and current crypto universe | universe_changed=%s | tz_cache=%s",
        universe_changed,
        cache_location,
    )
