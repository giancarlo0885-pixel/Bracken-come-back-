from __future__ import annotations

from datetime import datetime, timezone
import os
import time
from typing import Any, Iterable


def _truthy(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default) or default).strip().lower() == "true"


def _paper_only() -> bool:
    return (
        str(os.getenv("EXECUTION_MODE", "paper") or "paper").strip().lower() == "paper"
        and not _truthy("ENABLE_BROKER_SUBMISSION")
        and not _truthy("LIVE_TRADING_ARMED")
    )


def _grace_seconds() -> float:
    try:
        value = float(os.getenv("ROBINHOOD_CRYPTO_PAPER_QUOTE_GRACE_SECONDS", "30"))
    except ValueError:
        value = 30.0
    return min(60.0, max(3.0, value))


def install_robinhood_quote_resilience(worker: Any) -> bool:
    """Repair transient/partial Robinhood book reads for paper execution.

    A batch response may occasionally omit an otherwise tradable symbol. Missing
    symbols get one immediate single-symbol retry. In paper-only mode, if that
    retry also fails, a previously authenticated book may be reused for at most a
    short grace window. This keeps held-position repricing and paper learning
    continuous without permitting stale or fallback prices into live execution.
    """
    if getattr(worker, "_robinhood_quote_resilience_installed", False):
        return False
    provider = getattr(worker, "_robinhood_current_marketdata_provider", None)
    if provider is None:
        return False

    from robinhood_current_marketdata_runtime import snapshot_from_robinhood_quote

    original_snapshots = provider.snapshots

    def resilient_snapshots(symbols: Iterable[str]):
        requested = list(dict.fromkeys(str(symbol or "").upper().strip() for symbol in symbols if str(symbol or "").strip()))
        results = dict(original_snapshots(requested) or {})
        missing = [symbol for symbol in requested if symbol not in results]
        if not missing:
            return results

        for symbol in missing:
            try:
                records = provider.client.best_bid_ask_quotes(symbol)
            except Exception:
                records = []
            quote = next(
                (
                    item
                    for item in records or []
                    if isinstance(item, dict)
                    and str(item.get("symbol") or "").upper().strip() == symbol
                ),
                None,
            )
            if quote is None:
                continue
            read_time = datetime.now(timezone.utc).isoformat()
            snapshot = snapshot_from_robinhood_quote(symbol, quote, fetched_at=read_time)
            if snapshot is None:
                continue
            inserted_at = time.monotonic()
            with provider._lock:
                provider._cache[symbol] = (inserted_at, snapshot)
            results[symbol] = snapshot

        if not _paper_only():
            return results

        now = time.monotonic()
        grace = _grace_seconds()
        unresolved = [symbol for symbol in requested if symbol not in results]
        if unresolved:
            with provider._lock:
                for symbol in unresolved:
                    cached = provider._cache.get(symbol)
                    if not cached:
                        continue
                    inserted_at, snapshot = cached
                    if now - inserted_at <= grace:
                        results[symbol] = snapshot
        return results

    provider.snapshots = resilient_snapshots
    worker._robinhood_quote_resilience_installed = True
    worker.log.info(
        "CRYPTO | ROBINHOOD QUOTE RESILIENCE | single_symbol_retry=ON | paper_grace_seconds=%.1f | live_grace=OFF",
        _grace_seconds(),
    )
    return True
