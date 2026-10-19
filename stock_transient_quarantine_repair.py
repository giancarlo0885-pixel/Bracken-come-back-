from __future__ import annotations

import logging
import threading
import time
from typing import Any


log = logging.getLogger("stock-transient-quarantine")
_MAX_TRANSIENT_LOG_KEYS = 2048
_TRANSIENT_LOG_SECONDS = 300.0

_TRANSIENT_FAILURES = {
    "empty_history",
    "empty_fast_history",
    "YFRateLimitError",
    "ReadTimeout",
    "ConnectTimeout",
    "Timeout",
    "ConnectionError",
}


def install_stock_transient_quarantine_repair(worker: Any) -> None:
    """Keep temporary market-data outages out of invalid-symbol quarantine.

    ``provider_router`` already owns provider/capability/symbol cooldowns for rate
    limits and temporary data unavailability. The worker-level invalid-symbol
    quarantine is reserved for durable identity/symbol failures. Previously an
    empty history caused by a Yahoo/provider rate limit was persisted as an
    invalid symbol and eventually filtered the entire stock watchlist to zero.

    This stock-only repair ignores known transient availability failures and
    removes only stale transient ``market_data`` quarantine rows. Genuine symbol,
    identity, scope and provider-native mismatches continue through the original
    quarantine path unchanged.
    """
    original = worker._v39_quarantine_symbol
    if getattr(original, "_oracle_stock_transient_aware", False):
        return
    last_logged: dict[tuple[str, str], float] = {}
    log_lock = threading.Lock()

    def transient_aware(symbol: str, provider: str, failure_type: str) -> None:
        failure = str(failure_type or "").strip()
        provider_name = str(provider or "").strip()
        if provider_name == "market_data" and failure in _TRANSIENT_FAILURES:
            normalized_symbol = str(symbol or "").upper().strip()
            key = (normalized_symbol, failure)
            now = time.monotonic()
            with log_lock:
                previous = last_logged.get(key)
                if previous is not None and now - previous < _TRANSIENT_LOG_SECONDS:
                    return
                if key not in last_logged and len(last_logged) >= _MAX_TRANSIENT_LOG_KEYS:
                    expired = [k for k, at in last_logged.items() if now - at >= _TRANSIENT_LOG_SECONDS]
                    for old_key in expired:
                        last_logged.pop(old_key, None)
                    if len(last_logged) >= _MAX_TRANSIENT_LOG_KEYS:
                        last_logged.pop(next(iter(last_logged)))
                last_logged[key] = now
            log.info(
                "STOCK TRANSIENT DATA COOLDOWN | symbol=%s | failure=%s | invalid_symbol_quarantine=SKIPPED | provider_cooldown=UNCHANGED",
                normalized_symbol,
                failure,
            )
            return
        original(symbol, provider, failure_type)

    transient_aware._oracle_stock_transient_aware = True
    worker._v39_quarantine_symbol = transient_aware

    try:
        from database import connect

        with connect() as conn:
            result = conn.execute(
                """
                DELETE FROM invalid_symbol_quarantine
                WHERE provider='market_data'
                  AND failure_type = ANY(%s)
                """,
                (sorted(_TRANSIENT_FAILURES),),
            )
            restored = int(getattr(result, "rowcount", 0) or 0)
        log.warning(
            "STOCK TRANSIENT QUARANTINE REPAIR | restored_rows=%d | durable_invalid_symbol_evidence=PRESERVED",
            restored,
        )
    except Exception as exc:
        log.warning("STOCK TRANSIENT QUARANTINE REPAIR | cleanup=DEFERRED | reason=%s", exc.__class__.__name__)
