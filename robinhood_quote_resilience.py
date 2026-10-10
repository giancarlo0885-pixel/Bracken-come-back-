from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
from decimal import Decimal, InvalidOperation
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


# Fixed, small diagnostic sizes. These are quote requests, never orders.
# No fallback is authorized for assets or quantities outside this list.
_PAPER_ESTIMATE_QUANTITIES = {"BTC-USD": "0.001", "ETH-USD": "0.01"}
_MAX_ESTIMATE_AGE_SECONDS = 10.0
_MAX_ESTIMATE_FUTURE_SECONDS = 2.0


def _atomic_estimated_both_quote(
    records: Any, symbol: str, quantity: str,
) -> dict[str, Any] | None:
    """Normalize one or two broker-provided rows from ONE estimated-price call.

    The v2 endpoint may return separate bid and ask rows for side=both. Do not
    infer a missing price or combine different symbols, sizes or market moments.
    Only an exact bid+ask pair from the same response, at timestamps within one
    second, may form a reference. The older timestamp governs freshness.
    """
    if not isinstance(records, list):
        return None
    if len(records) == 1 and isinstance(records[0], dict):
        return records[0]
    if len(records) != 2 or not all(isinstance(row, dict) for row in records):
        return None

    by_side: dict[str, dict[str, Any]] = {}
    observed_times: list[datetime] = []
    for row in records:
        side = str(row.get("side") or "").lower().strip()
        if side not in {"bid", "ask"} or side in by_side:
            return None
        if str(row.get("symbol") or "").upper().strip() != symbol:
            return None
        try:
            if Decimal(str(row.get("quantity"))) != Decimal(quantity):
                return None
            observed = datetime.fromisoformat(
                str(row.get("timestamp") or "").replace("Z", "+00:00")
            )
        except (ValueError, TypeError, InvalidOperation):
            return None
        if observed.tzinfo is None:
            return None
        by_side[side] = row
        observed_times.append(observed.astimezone(timezone.utc))

    if set(by_side) != {"bid", "ask"}:
        return None
    if abs((observed_times[0] - observed_times[1]).total_seconds()) > 1.0:
        return None
    bid = _first_present(by_side["bid"], "bid", "bid_price")
    ask = _first_present(by_side["ask"], "ask", "ask_price")
    if bid is None or ask is None:
        return None

    return {
        "symbol": symbol,
        "side": "both",
        "quantity": quantity,
        "timestamp": min(observed_times).isoformat(),
        "bid": bid,
        "ask": ask,
    }


_independent_quote_checked_at: dict[str, float] = {}


def _independent_coinbase_quote_check(
    symbol: str, crossed_quote: dict[str, Any], worker: Any,
) -> str:
    """Read-only second-provider diagnostic. Never returns an executable quote.

    Coinbase Exchange public ticker supplies its own UTC timestamp and two
    sides. Identity is anchored to the fixed product endpoint; stale, crossed,
    implausible or divergent prices are not treated as valid evidence.
    """
    if not _paper_only() or symbol not in {"BTC-USD", "ETH-USD"}:
        return "DISABLED"
    now = time.monotonic()
    if now - _independent_quote_checked_at.get(symbol, -1e12) < 60:
        return "THROTTLED"
    _independent_quote_checked_at[symbol] = now
    status = "UNAVAILABLE"
    try:
        import requests

        response = requests.get(
            f"https://api.exchange.coinbase.com/products/{symbol}/ticker",
            headers={"Accept": "application/json", "User-Agent": "Oracle-paper-quote-diagnostic/1"},
            timeout=3,
        )
        if response.status_code != 200:
            status = "PROVIDER_HTTP_FAILURE"
        else:
            data = response.json()
            if not isinstance(data, dict):
                status = "INVALID_PAYLOAD"
            else:
                observed = datetime.fromisoformat(
                    str(data.get("time") or "").replace("Z", "+00:00")
                )
                if observed.tzinfo is None:
                    status = "MISSING_TIMEZONE"
                else:
                    age = (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
                    bid = Decimal(str(data.get("bid")))
                    ask = Decimal(str(data.get("ask")))
                    if age < -2 or age > 10:
                        status = "STALE_OR_FUTURE"
                    elif not all(v.is_finite() and v > 0 for v in (bid, ask)):
                        status = "INVALID_PRICE"
                    elif ask < bid:
                        status = "CROSSED"
                    else:
                        primary_bid = Decimal(str(_first_present(crossed_quote, "bid", "bid_price")))
                        primary_ask = Decimal(str(_first_present(crossed_quote, "ask", "ask_price")))
                        mid = (bid + ask) / 2
                        primary_mid = (primary_bid + primary_ask) / 2
                        divergence = abs(mid - primary_mid) / mid * 100
                        spread = (ask - bid) / mid * 100
                        if not all(v.is_finite() and v > 0 for v in (primary_bid, primary_ask)):
                            status = "INVALID_PRIMARY"
                        elif divergence > Decimal("0.75"):
                            status = "DIVERGENT"
                        elif spread > Decimal("1.50"):
                            status = "WIDE_SPREAD"
                        else:
                            status = "INDEPENDENT_REFERENCE_COHERENT"
    except Exception:
        status = "FETCH_OR_VALIDATION_FAILURE"
    worker.log.info(
        "CRYPTO | INDEPENDENT QUOTE CHECK | symbol=%s | provider=Coinbase Exchange | "
        "status=%s | execution_impact=NONE | broker_submission=NONE",
        symbol, status,
    )
    return status


def _paper_estimated_snapshot(
    provider: Any,
    symbol: str,
    crossed_quote: dict[str, Any],
    worker: Any,
) -> Any | None:
    """Read an independently generated, size-specific Robinhood v2 estimate.

    Only used after a crossed best-bid/ask fails all retries, in fully disarmed
    paper mode. It is a marked paper reference, *not* a replacement broker BBO
    and never authorizes entries. Reject wrong identity, size, timestamp,
    non-positive/crossed books, wide spreads and price divergence.
    """
    def reject(reason: str) -> None:
        worker.log.info(
            "CRYPTO | ROBINHOOD PAPER ESTIMATE | symbol=%s | status=REJECTED | reason=%s | entry_eligible=false",
            symbol, reason,
        )
        return None

    if not _paper_only():
        return reject("PAPER_ONLY_CONTRACT_UNMET")
    quantity = _PAPER_ESTIMATE_QUANTITIES.get(symbol)
    if not quantity:
        return reject("REFERENCE_SIZE_UNAVAILABLE")
    try:
        records = provider.client.estimated_price(symbol, "both", quantity)
    except Exception as exc:
        worker.log.info(
            "CRYPTO | ROBINHOOD PAPER ESTIMATE | symbol=%s | status=ERROR | error=%s",
            symbol, exc.__class__.__name__,
        )
        return None

    # Report response shape, never prices, account data, or credentials.
    first = records[0] if isinstance(records, list) and records and isinstance(records[0], dict) else {}
    worker.log.info(
        "CRYPTO | ROBINHOOD PAPER ESTIMATE SHAPE | symbol=%s | rows=%s | match=%s | side=%s | two_sided=%s | has_qty=%s | has_ts=%s",
        symbol, len(records) if isinstance(records, list) else "not_list",
        str(first.get("symbol") or "").upper().strip() == symbol,
        str(first.get("side") or "").lower().strip() or "none",
        _first_present(first, "bid", "bid_price") is not None and _first_present(first, "ask", "ask_price") is not None,
        first.get("quantity") not in (None, ""),
        first.get("timestamp") not in (None, ""),
    )

    # Both sides must come from the same read and nearly identical provider
    # times. Never pair separate HTTP responses or infer a missing side.
    quote = _atomic_estimated_both_quote(records, symbol, quantity)
    if quote is None:
        return reject("UNPAIRED_OR_MISSING_ESTIMATE_SIDES")
    if str(quote.get("symbol") or "").upper().strip() != symbol:
        return reject("ESTIMATE_SYMBOL_MISMATCH")
    if str(quote.get("side") or "").lower().strip() not in {"bid", "ask", "both"}:
        return reject("ESTIMATE_SIDE_INVALID")
    try:
        if Decimal(str(quote.get("quantity"))) != Decimal(quantity):
            return reject("ESTIMATE_SIZE_MISMATCH")
        observed = datetime.fromisoformat(str(quote.get("timestamp") or "").replace("Z", "+00:00"))
        if observed.tzinfo is None:
            return reject("ESTIMATE_TIMESTAMP_NAIVE")
        age = (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
        if age > _MAX_ESTIMATE_AGE_SECONDS or age < -_MAX_ESTIMATE_FUTURE_SECONDS:
            return reject("ESTIMATE_TIMESTAMP_OUT_OF_WINDOW")
        limit = Decimal(str(os.getenv("ROBINHOOD_BROKER_MAX_SPREAD_PCT", "1.50")))
        divergence_limit = Decimal(str(os.getenv("ROBINHOOD_BROKER_PRICE_TOLERANCE_PCT", "0.75")))
        if not limit.is_finite() or limit < 0 or not divergence_limit.is_finite() or divergence_limit < 0:
            return reject("INVALID_EXISTING_QUOTE_LIMITS")
        bid = Decimal(str(_first_present(quote, "bid", "bid_price")))
        ask = Decimal(str(_first_present(quote, "ask", "ask_price")))
        crossed_bid = Decimal(str(_first_present(crossed_quote, "bid_price", "bid", "bid_inclusive_of_sell_spread")))
        crossed_ask = Decimal(str(_first_present(crossed_quote, "ask_price", "ask", "ask_inclusive_of_buy_spread")))
        if not all(v.is_finite() and v > 0 for v in (bid, ask, crossed_bid, crossed_ask)):
            return reject("NON_POSITIVE_OR_NON_FINITE_ESTIMATE")
        if ask < bid or crossed_ask >= crossed_bid:
            return reject("INCONSISTENT_ESTIMATE_OR_PRIMARY_BOOK")
        mid = (bid + ask) / Decimal("2")
        crossed_mid = (crossed_bid + crossed_ask) / Decimal("2")
        spread_pct = (ask - bid) / mid * Decimal("100")
        # Crossed book is used only as an independent sanity bound, never a price.
        difference_pct = abs(mid - crossed_mid) / mid * Decimal("100")
        if spread_pct > limit or difference_pct > divergence_limit:
            return reject("ESTIMATE_SPREAD_OR_DIVERGENCE_LIMIT")
    except (ValueError, TypeError, InvalidOperation, OverflowError):
        return reject("ESTIMATE_PARSE_INVALID")

    from robinhood_current_marketdata_runtime import snapshot_from_robinhood_quote

    fetched_at = datetime.now(timezone.utc).isoformat()
    snapshot = snapshot_from_robinhood_quote(symbol, quote, fetched_at=fetched_at)
    if snapshot is None:
        return reject("ESTIMATE_SNAPSHOT_INVALID")
    return replace(
        snapshot,
        timestamp=observed.astimezone(timezone.utc).isoformat(),
        source_capability="v2_estimated_price_paper_reference",
        source_identity=f"Robinhood Crypto:{symbol}:v2_estimated_price:qty={quantity}",
        cache_identity=f"robinhood_v2_paper_estimate:{symbol}:{quantity}",
        verification_basis="paper_estimate:robinhood_crypto_v2_size_specific",
        provider_quote_verified=False,
        paper_reference_verified=True,
    )


def _paper_grace_snapshot(snapshot: Any) -> Any:
    basis = str(getattr(snapshot, "verification_basis", "") or "")
    if basis.startswith("paper_grace:"):
        return snapshot
    return replace(
        snapshot,
        verification_basis=f"paper_grace:{basis or 'provider:robinhood_crypto_cached'}",
    )


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



def _sanitized_book_boundary(quote: dict[str, Any], symbol: str) -> dict[str, str]:
    """Allowlisted raw-to-normalized quote evidence; never log payloads or credentials."""
    def value(*keys: str) -> str:
        raw = _first_present(quote, *keys)
        if raw is None:
            return "missing"
        try:
            number = Decimal(str(raw))
            return str(number) if number.is_finite() and number > 0 else "invalid"
        except (InvalidOperation, ValueError, TypeError):
            return "invalid"

    raw_symbol = str(quote.get("symbol") or "").upper().strip()
    raw_time = quote.get("timestamp")
    try:
        parsed = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
        timestamp = parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo else "invalid"
    except (ValueError, TypeError, OverflowError):
        timestamp = "invalid"
    return {
        "requested_symbol": symbol,
        "raw_symbol_match": str(raw_symbol == symbol).lower(),
        "raw_bid": value("bid"),
        "raw_ask": value("ask"),
        "normalized_bid": value("bid_price", "bid", "bid_inclusive_of_sell_spread"),
        "normalized_ask": value("ask_price", "ask", "ask_inclusive_of_buy_spread"),
        "source": "Robinhood Crypto:best_bid_ask",
        "source_timestamp_utc": timestamp,
        "timestamp_basis": "provider_payload" if raw_time is not None else "missing",
    }


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
        # A cached paper estimate must never become an execution quote if mode
        # changes without a process restart. Treat it as absent outside paper.
        if not _paper_only():
            results = {
                symbol: snapshot for symbol, snapshot in results.items()
                if not str(getattr(snapshot, "verification_basis", "") or "").startswith("paper_estimate:")
            }
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
            last_crossed_quote = None
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
                        last_crossed_quote = quote
                    if reason == "CROSSED_BOOK" and _paper_only():
                        boundary = _sanitized_book_boundary(quote, symbol)
                        worker.log.info(
                            "CRYPTO | ROBINHOOD BOOK BOUNDARY | symbol=%s | raw_symbol_match=%s | "
                            "raw_bid=%s | raw_ask=%s | normalized_bid=%s | normalized_ask=%s | "
                            "source=%s | source_timestamp_utc=%s | timestamp_basis=%s | "
                            "execution_impact=NONE | broker_submission=NONE",
                            boundary["requested_symbol"], boundary["raw_symbol_match"],
                            boundary["raw_bid"], boundary["raw_ask"],
                            boundary["normalized_bid"], boundary["normalized_ask"],
                            boundary["source"], boundary["source_timestamp_utc"],
                            boundary["timestamp_basis"],
                        )
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

            if not recovered and crossed_attempts == attempts and last_crossed_quote is not None:
                paper_snapshot = _paper_estimated_snapshot(provider, symbol, last_crossed_quote, worker)
                if paper_snapshot is not None:
                    with provider._lock:
                        provider._cache[symbol] = (time.monotonic(), paper_snapshot)
                    results[symbol] = paper_snapshot
                    recovered = True
                    crossed_cycles.pop(symbol, None)
                    quality_until.pop(symbol, None)
                    worker.log.warning(
                        "CRYPTO | ROBINHOOD PAPER ESTIMATE RECOVERY | symbol=%s | "
                        "source=V2_ESTIMATED_PRICE | primary_book=CROSSED | "
                        "entry_eligible=false | broker_submission=NONE | live_trading=DISARMED",
                        symbol,
                    )

            if not recovered and crossed_attempts == attempts and last_crossed_quote is not None:
                _independent_coinbase_quote_check(symbol, last_crossed_quote, worker)

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
                        results[symbol] = _paper_grace_snapshot(snapshot)
                        worker.log.info(
                            "CRYPTO | ROBINHOOD PAPER QUOTE GRACE | symbol=%s | age_seconds=%.2f | max_seconds=%.2f | entry_eligible=false",
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
