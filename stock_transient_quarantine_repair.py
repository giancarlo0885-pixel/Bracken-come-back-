from __future__ import annotations

import logging
from typing import Any


log = logging.getLogger("stock-transient-quarantine")

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

    def transient_aware(symbol: str, provider: str, failure_type: str) -> None:
        failure = str(failure_type or "").strip()
        provider_name = str(provider or "").strip()
        if provider_name == "market_data" and failure in _TRANSIENT_FAILURES:
            log.info(
                "STOCK TRANSIENT DATA COOLDOWN | symbol=%s | failure=%s | invalid_symbol_quarantine=SKIPPED",
                str(symbol or "").upper().strip(),
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
