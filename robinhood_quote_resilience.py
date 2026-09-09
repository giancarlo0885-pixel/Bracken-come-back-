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


def _retry_attempts() -> int:
    try:
        value = int(os.getenv("ROBINHOOD_CRYPTO_QUOTE_RETRY_ATTEMPTS", "3"))
    except ValueError:
        value = 3
    return min(5, max(1, value))


def _retry_delay_seconds() -> float:
    try:
        value = float(os.getenv("ROBINHOOD_CRYPTO_QUOTE_RETRY_DELAY_SECONDS", "0.12"))
    except ValueError:
        value = 0.12
    return min(0.50, max(0.0, value))


def _quality_failure_threshold() -> int:
    try:
        value = int(os.getenv("ROBINHOOD_CRYPTO_CROSSED_BOOK_FAILURE_CYCLES", "2"))
    except ValueError:
        value = 2
    return min(5, max(1, value))


def _quality_cooldown_seconds() -> float:
    try:
        value = float(os.getenv("ROBINHOOD_CRYPTO_CROSSED_BOOK_COOLDOWN_SECONDS", "120"))
    except ValueError:
        value = 120.0
    return min(600.0, max(30.0, value))


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
    """Repair transient books and cool repeated crossed books for non-held assets.

    Quality cooldown is paper-only and never suppresses quote attempts for held
    positions, so protective monitoring/risk exits remain intact. Live quote
    grace remains disabled and no bid/ask values are swapped or fabricated.
    """
    if getattr(worker, "_robinhood_quote_resilience_installed", False):
        return False
    provider = getattr(worker, "_robinhood_current_marketdata_provider", None)
    if provider is None:
        return False

    from robinhood_current_marketdata_runtime import snapshot_from_robinhood_quote

    original_snapshots = provider.snapshots
    crossed_cycles: dict[str, int] = {}
    quality_until: dict[str, float] = {}
    provider._oracle_quality_quarantined_symbols = set()

    def _held_symbols() -> set[str]:
        try:
            fn = getattr(worker, "_held_symbols", None)
            if callable(fn):
                return {
                    str(symbol or "").upper().strip()
                    for symbol in (fn("crypto") or set())
                    if str(symbol or "").strip()
                }
        except Exception:
            pass
        return set()

    def _refresh_quality_state(now: float, held: set[str]) -> set[str]:
        for symbol, expires in list(quality_until.items()):
            if expires <= now:
                quality_until.pop(symbol, None)
                crossed_cycles.pop(symbol, None)
        active = {
            symbol for symbol, expires in quality_until.items()
            if expires > now and symbol not in held
        }
        provider._oracle_quality_quarantined_symbols = set(active)
        return active

    def resilient_snapshots(symbols: Iterable[str]):
        requested = list(dict.fromkeys(str(symbol or "").upper().strip() for symbol in symbols if str(symbol or "").strip()))
        supported = provider.tradable_symbols()
        held = _held_symbols()
        now_mono = time.monotonic()
        quality_quarantined = _refresh_quality_state(now_mono, held)

        eligible_supported = [symbol for symbol in requested if symbol in supported]
        unsupported = [symbol for symbol in requested if symbol not in supported]
        quality_blocked = [
            symbol for symbol in eligible_supported
            if symbol in quality_quarantined and symbol not in held
        ]
        eligible = [symbol for symbol in eligible_supported if symbol not in quality_blocked]

        if unsupported:
            worker.log.info(
                "CRYPTO | ROBINHOOD UNSUPPORTED PAIRS | symbols=%s | action=SKIP_BEST_BID_ASK | reason=NOT_API_TRADABLE",
                ",".join(unsupported),
            )
        if quality_blocked:
            worker.log.info(
                "CRYPTO | ROBINHOOD BOOK QUALITY COOLDOWN | symbols=%s | action=SKIP_NONHELD_QUOTE | "
                "reason=REPEATED_CROSSED_BOOK | live_trading=DISARMED",
                ",".join(quality_blocked),
            )

        results = dict(original_snapshots(eligible) or {})
        for symbol in results:
            crossed_cycles.pop(symbol, None)
            quality_until.pop(symbol, None)
        _refresh_quality_state(time.monotonic(), held)

        missing = [symbol for symbol in eligible if symbol not in results]
        if not missing:
            return results

        attempts = _retry_attempts()
        retry_delay = _retry_delay_seconds()
        threshold = _quality_failure_threshold()
        cooldown = _quality_cooldown_seconds()
        for symbol in missing:
            crossed_attempts = 0
            recovered = False
            for attempt in range(1, attempts + 1):
                try:
                    records = provider.client.best_bid_ask_quotes(symbol)
                except Exception as exc:
                    worker.log.info(
                        "CRYPTO | ROBINHOOD SINGLE QUOTE RETRY | symbol=%s | attempt=%s/%s | status=ERROR | error=%s",
                        symbol,
                        attempt,
                        attempts,
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
                        "CRYPTO | ROBINHOOD SINGLE QUOTE RETRY | symbol=%s | attempt=%s/%s | status=OMITTED | api_tradable=true",
                        symbol,
                        attempt,
                        attempts,
                    )
                else:
                    read_time = datetime.now(timezone.utc).isoformat()
                    snapshot = snapshot_from_robinhood_quote(symbol, quote, fetched_at=read_time)
                    if snapshot is not None:
                        inserted_at = time.monotonic()
                        with provider._lock:
                            provider._cache[symbol] = (inserted_at, snapshot)
                        results[symbol] = snapshot
                        crossed_cycles.pop(symbol, None)
                        quality_until.pop(symbol, None)
                        recovered = True
                        worker.log.info(
                            "CRYPTO | ROBINHOOD SINGLE QUOTE RETRY | symbol=%s | attempt=%s/%s | status=RECOVERED | api_tradable=true",
                            symbol,
                            attempt,
                            attempts,
                        )
                        break
                    reason = _invalid_book_reason(quote)
                    if reason == "CROSSED_BOOK":
                        crossed_attempts += 1
                    worker.log.info(
                        "CRYPTO | ROBINHOOD SINGLE QUOTE RETRY | symbol=%s | attempt=%s/%s | status=INVALID_BOOK | reason=%s | public_keys=%s | api_tradable=true",
                        symbol,
                        attempt,
                        attempts,
                        reason,
                        _public_quote_keys(quote),
                    )

                if attempt < attempts and retry_delay > 0:
                    time.sleep(retry_delay)

            if not recovered and crossed_attempts == attempts:
                cycles = crossed_cycles.get(symbol, 0) + 1
                crossed_cycles[symbol] = cycles
                if _paper_only() and symbol not in held and cycles >= threshold:
                    quality_until[symbol] = time.monotonic() + cooldown
                    _refresh_quality_state(time.monotonic(), held)
                    worker.log.info(
                        "CRYPTO | ROBINHOOD BOOK QUALITY QUARANTINE | symbol=%s | crossed_cycles=%d | threshold=%d | "
                        "cooldown_seconds=%.0f | held=false | broker_submission=NONE | live_trading=DISARMED",
                        symbol,
                        cycles,
                        threshold,
                        cooldown,
                    )
            elif crossed_attempts < attempts and not recovered:
                crossed_cycles.pop(symbol, None)

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
        "CRYPTO | ROBINHOOD QUOTE RESILIENCE | single_symbol_retry=ON | retry_attempts=%s | retry_delay_seconds=%.2f | "
        "unsupported_pair_filter=ON | crossed_book_quality_cooldown=ON | failure_cycles=%d | cooldown_seconds=%.0f | "
        "paper_grace_seconds=%.1f | live_grace=OFF",
        _retry_attempts(),
        _retry_delay_seconds(),
        _quality_failure_threshold(),
        _quality_cooldown_seconds(),
        _grace_seconds(),
    )
    return True
