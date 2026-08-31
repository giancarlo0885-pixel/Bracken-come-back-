from __future__ import annotations

import logging
import math
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
    and place that exact symbol/period/interval on a cooldown. A per-key lock
    prevents concurrent fast/deep scans from issuing the same doomed request.

    This function does not make Yahoo provider-verified. Existing market_data
    logic remains responsible for exact identity/freshness checks and the crypto
    execution guard still requires Coinbase consensus for Yahoo paper marks.
    """
    requested = str(symbol or "").upper().strip()
    key = _key(requested, period, interval)
    if not requested or _cooldown_active(key):
        return pd.DataFrame()

    lock = _key_lock(key)
    with lock:
        if _cooldown_active(key):
            return pd.DataFrame()
        try:
            ticker = market_data_module.yf.Ticker(requested)
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
                "YAHOO REFERENCE UNAVAILABLE | symbol=%s | period=%s | interval=%s | reason=%s | cooldown=active",
                requested,
                period,
                interval,
                exc.__class__.__name__,
            )
            return pd.DataFrame()

        normalized = market_data_module._normalize(data, requested)
        if normalized is None or normalized.empty:
            _mark_unavailable(key)
            log.info(
                "YAHOO REFERENCE UNAVAILABLE | symbol=%s | period=%s | interval=%s | reason=EMPTY_HISTORY | cooldown=active",
                requested,
                period,
                interval,
            )
            return pd.DataFrame()

        # Reject a non-finite/invalid terminal close before the router can treat
        # the frame as a usable paper reference.
        try:
            close = pd.to_numeric(normalized["Close"], errors="coerce").dropna()
            latest = float(close.iloc[-1]) if len(close) else float("nan")
        except Exception:
            latest = float("nan")
        if not math.isfinite(latest) or latest <= 0:
            _mark_unavailable(key)
            return pd.DataFrame()

        _clear_unavailable(key)
        return market_data_module._stamp_history(
            normalized,
            requested,
            requested,
            "Yahoo Finance",
            interval,
        )


def install_yahoo_runtime_reliability() -> None:
    """Install the quiet, single-flight Yahoo fallback for all worker scans."""
    global _INSTALLED
    if _INSTALLED:
        return
    import market_data

    def patched(symbol: str, period: str, interval: str) -> pd.DataFrame:
        return yahoo_reference_history(market_data, symbol, period, interval)

    market_data._download_yahoo = patched
    _INSTALLED = True
    log.info("Installed Yahoo fallback single-flight and unavailable-symbol cooldown")
