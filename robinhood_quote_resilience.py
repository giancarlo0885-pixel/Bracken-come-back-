from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
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
        value = float(os.getenv("ROBINHOOD_CRYPTO_PAPER_QUOTE_GRACE_SECONDS", "60"))
    except ValueError:
        value = 60.0
    return min(60.0, max(3.0, value))


def _first_present(quote: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = quote.get(key)
        if value not in (None, ""):
            return value
    return None


def _invalid_book_reason(quote: dict[str, Any]) -> str:
    if not isinstance(quote, dict):
        return "NOT_OBJECT"
    bid_raw = _first_present(quote, "bid_price", "bid", "bid_inclusive_of_sell_spread")
    ask_raw = _first_present(quote, "ask_price", "ask", "ask_inclusive_of_buy_spread")
    if bid_raw is None:
        return "BID_MISSING"
    if ask_raw is None:
        return "ASK_MISSING"
    try:
        bid = Decimal(str(bid_raw))
        ask = Decimal(str(ask_raw))
    except Exception:
        return f"NON_NUMERIC:bid_type={type(bid_raw).__name__}:ask_type={type(ask_raw).__name__}"
    if not bid.is_finite() or not ask.is_finite():
        return "NON_FINITE"
    if bid <= 0 or ask <= 0:
        return "NON_POSITIVE"
    if ask < bid:
        return "CROSSED_BOOK"
    return "UNKNOWN"


def _public_quote_keys(quote: dict[str, Any]) -> str:
    allowed = {
        "symbol",
        "bid",
        "ask",
        "bid_price",
        "ask_price",
        "bid_inclusive_of_sell_spread",
        "ask_inclusive_of_buy_spread",
        "bid_inclusive_of_sell_fee",
        "ask_inclusive_of_buy_fee",
        "timestamp",
    }
    return ",".join(sorted(str(key) for key in quote.keys() if str(key) in allowed)) or "none"


def install_robinhood_quote_resilience(worker: Any) -> bool:
    """Repair transient/partial Robinhood book reads for paper execution."""
    if getattr(worker, "_robinhood_quote_resilience_installed", False):
        return False
    provider = getattr(worker, "_robinhood_current_marketdata_provider", None)
    if provider is None:
        return False

    from robinhood_current_marketdata_runtime import snapshot_from_robinhood_quote

    original_snapshots = provider.snapshots

    def resilient_snapshots(symbols: Iterable[str]):
        requested = list(dict.fromkeys(str(symbol or "").upper().strip() for symbol in symbols if str(symbol or "").strip()))
        supported = provider.tradable_symbols()
        eligible = [symbol for symbol in requested if symbol in supported]
        unsupported = [symbol for symbol in requested if symbol not in supported]
        if unsupported:
            worker.log.info(
                "CRYPTO | ROBINHOOD UNSUPPORTED PAIRS | symbols=%s | action=SKIP_BEST_BID_ASK | reason=NOT_API_TRADABLE",
                ",".join(unsupported),
            )

        results = dict(original_snapshots(eligible) or {})
        missing = [symbol for symbol in eligible if symbol not in results]
        if not missing:
            return results

        for symbol in missing:
            try:
                records = provider.client.best_bid_ask_quotes(symbol)
            except Exception as exc:
                worker.log.info(
                    "CRYPTO | ROBINHOOD SINGLE QUOTE RETRY | symbol=%s | status=ERROR | error=%s",
                    symbol,
                    exc.__class__.__name__,
                )
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
                worker.log.info(
                    "CRYPTO | ROBINHOOD SINGLE QUOTE RETRY | symbol=%s | status=OMITTED | api_tradable=true",
                    symbol,
                )
                continue
            read_time = datetime.now(timezone.utc).isoformat()
            snapshot = snapshot_from_robinhood_quote(symbol, quote, fetched_at=read_time)
            if snapshot is None:
                worker.log.info(
                    "CRYPTO | ROBINHOOD SINGLE QUOTE RETRY | symbol=%s | status=INVALID_BOOK | reason=%s | public_keys=%s | api_tradable=true",
                    symbol,
                    _invalid_book_reason(quote),
                    _public_quote_keys(quote),
                )
                continue
            inserted_at = time.monotonic()
            with provider._lock:
                provider._cache[symbol] = (inserted_at, snapshot)
            results[symbol] = snapshot
            worker.log.info(
                "CRYPTO | ROBINHOOD SINGLE QUOTE RETRY | symbol=%s | status=RECOVERED | api_tradable=true",
                symbol,
            )

        if not _paper_only():
            return results

        now = time.monotonic()
        grace = _grace_seconds()
        unresolved = [symbol for symbol in eligible if symbol not in results]
        if unresolved:
            with provider._lock:
                for symbol in unresolved:
                    cached = provider._cache.get(symbol)
                    if not cached:
                        continue
                    inserted_at, snapshot = cached
                    age = now - inserted_at
                    if age <= grace:
                        results[symbol] = snapshot
                        worker.log.info(
                            "CRYPTO | ROBINHOOD PAPER QUOTE GRACE | symbol=%s | age_seconds=%.2f | max_seconds=%.2f",
                            symbol,
                            age,
                            grace,
                        )
        return results

    provider.snapshots = resilient_snapshots
    worker._robinhood_quote_resilience_installed = True
    worker.log.info(
        "CRYPTO | ROBINHOOD QUOTE RESILIENCE | single_symbol_retry=ON | unsupported_pair_filter=ON | paper_grace_seconds=%.1f | live_grace=OFF",
        _grace_seconds(),
    )
    return True
