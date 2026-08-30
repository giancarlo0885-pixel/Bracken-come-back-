from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import logging
import os
import threading
import time
from typing import Any

import requests

from market_sessions import parse_utc

log = logging.getLogger("crypto-execution-guard")

_COINBASE_CACHE_LOCK = threading.Lock()
_COINBASE_CACHE: dict[str, tuple[float, dict[str, Any] | None, str | None]] = {}


def _symbol(signal: Any) -> str:
    if isinstance(signal, dict):
        value = signal.get("symbol")
    else:
        value = getattr(signal, "symbol", None)
    return str(value or "").upper().strip()


def _live_execution_mode() -> bool:
    return str(os.getenv("EXECUTION_MODE", "paper") or "paper").lower().strip() == "live"


def _paper_yahoo_reference(quote: dict[str, Any]) -> bool:
    # The execution normalizer intentionally emits a compact quote payload and
    # may omit internal paper-reference flags. In provider_router Yahoo is never
    # a provider-verified execution source; it becomes execution-eligible only
    # through the explicit paper fallback. Therefore provider identity alone is
    # the durable boundary here.
    return str(quote.get("provider") or "").strip().lower() == "yahoo finance"


def _coinbase_quote(symbol: str) -> tuple[dict[str, Any] | None, str | None]:
    """Return a cached public Coinbase Exchange ticker for an exact USD pair."""
    requested = str(symbol or "").upper().strip()
    if not requested.endswith("-USD"):
        return None, "COINBASE_PAIR_UNSUPPORTED"

    try:
        ttl_seconds = max(15, int(os.getenv("COINBASE_REFERENCE_TTL_SECONDS", "60")))
    except ValueError:
        ttl_seconds = 60

    now = time.monotonic()
    with _COINBASE_CACHE_LOCK:
        cached = _COINBASE_CACHE.get(requested)
        if cached and now - cached[0] <= ttl_seconds:
            return cached[1], cached[2]

    quote: dict[str, Any] | None = None
    error: str | None = None
    try:
        response = requests.get(
            f"https://api.exchange.coinbase.com/products/{requested}/ticker",
            headers={"cache-control": "no-cache", "user-agent": "GARIBALDI-MARKET-ORACLE/1.0"},
            timeout=5,
        )
        if response.status_code == 404:
            error = "COINBASE_PAIR_UNAVAILABLE"
        else:
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                error = "COINBASE_QUOTE_INVALID"
            else:
                quote = {
                    "symbol": requested,
                    "price": payload.get("price"),
                    "bid": payload.get("bid"),
                    "ask": payload.get("ask"),
                    "timestamp": payload.get("time"),
                    "provider": "Coinbase Exchange",
                }
    except Exception:
        error = "COINBASE_REFERENCE_UNAVAILABLE"

    with _COINBASE_CACHE_LOCK:
        _COINBASE_CACHE[requested] = (now, quote, error)
    return quote, error


def _coinbase_reference_validation(symbol: str, oracle_price: Any) -> dict[str, Any]:
    """Cross-check a Yahoo paper mark against an independent exchange book."""
    quote, error = _coinbase_quote(symbol)
    if quote is None:
        return {"ok": False, "reason": error or "COINBASE_QUOTE_MISSING"}

    try:
        bid = float(quote.get("bid") or 0)
        ask = float(quote.get("ask") or 0)
        reference = float(oracle_price or 0)
        max_diff_pct = max(0.0, float(os.getenv("COINBASE_REFERENCE_MAX_DIFF_PCT", "1.00")))
        max_spread_pct = max(0.0, float(os.getenv("COINBASE_REFERENCE_MAX_SPREAD_PCT", "1.50")))
        max_age_seconds = max(30, int(os.getenv("COINBASE_REFERENCE_MAX_AGE_SECONDS", "300")))
    except (TypeError, ValueError):
        return {"ok": False, "reason": "COINBASE_REFERENCE_CONFIG_INVALID"}

    if bid <= 0 or ask <= 0 or ask < bid or reference <= 0:
        return {"ok": False, "reason": "COINBASE_QUOTE_INVALID"}

    timestamp = parse_utc(quote.get("timestamp"))
    if timestamp is None:
        return {"ok": False, "reason": "COINBASE_TIMESTAMP_INVALID"}
    age_seconds = max(0.0, (datetime.now(timezone.utc) - timestamp).total_seconds())
    if age_seconds > max_age_seconds:
        return {"ok": False, "reason": "COINBASE_QUOTE_STALE", "age_seconds": age_seconds}

    mid = (bid + ask) / 2.0
    spread_pct = ((ask - bid) / mid) * 100.0 if mid > 0 else 100.0
    difference_pct = abs(reference - mid) / mid * 100.0 if mid > 0 else 100.0
    if spread_pct > max_spread_pct:
        return {
            "ok": False,
            "reason": "COINBASE_SPREAD_TOO_WIDE",
            "spread_pct": spread_pct,
            "difference_pct": difference_pct,
        }
    if difference_pct > max_diff_pct:
        return {
            "ok": False,
            "reason": "COINBASE_PRICE_DIVERGENCE",
            "spread_pct": spread_pct,
            "difference_pct": difference_pct,
        }
    return {
        "ok": True,
        "reason": "COINBASE_REFERENCE_CONFIRMED",
        "reference_provider": "Coinbase Exchange",
        "reference_price": mid,
        "reference_timestamp": timestamp.isoformat(),
        "spread_pct": spread_pct,
        "difference_pct": difference_pct,
    }


def _broker_quote_map(symbols: list[str]) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Fetch one read-only Robinhood quote batch for future live execution.

    Paper mode never calls this function. A missing/invalid Robinhood connection
    fails closed when live mode is selected.
    """
    if not symbols:
        return {}, None
    try:
        from robinhood_crypto_api import RobinhoodCryptoClient

        client = RobinhoodCryptoClient()
        configured = client.configured()
        if not configured.get("ok"):
            return {}, str(configured.get("reason") or "Robinhood not configured")
        records = client.best_bid_ask_quotes(*symbols)
        return {
            str(item.get("symbol") or "").upper().strip(): item
            for item in records
            if isinstance(item, dict) and str(item.get("symbol") or "").strip()
        }, None
    except Exception as exc:
        return {}, str(exc)


def install_crypto_execution_quote_guard(worker: Any) -> None:
    """Fail closed when execution price truth is not independently defensible.

    Paper mode keeps provider-verified quotes unchanged. Yahoo paper fallback
    quotes must also agree with Coinbase Exchange when they reach execution.
    Future live mode then adds Robinhood's own best bid/ask as the final broker
    truth. This guard never submits an order.
    """
    if getattr(worker, "_crypto_execution_quote_guard_installed", False):
        return

    import oracle_bot

    original_process_signals = worker.process_signals

    def guarded_process_signals(
        market: str,
        signals: Any,
        prices: dict[str, Any] | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        if str(market or "").lower() != "crypto":
            return original_process_signals(market, signals, prices, *args, **kwargs)

        quote_map = prices or {}
        verified_pairs: list[tuple[Any, str, dict[str, Any]]] = []
        skipped: list[str] = []
        for signal in list(signals or []):
            symbol = _symbol(signal)
            if not symbol:
                continue
            quote = oracle_bot._verified_quote_for(symbol, quote_map, "crypto")
            if quote is None:
                skipped.append(symbol)
                continue
            verified_pairs.append((signal, symbol, quote))

        if skipped:
            worker.log.info(
                "CRYPTO | EXECUTION SKIP | no verified live quote | affected_symbols=%d | sample=%s",
                len(skipped),
                ",".join(skipped[:8]),
            )

        # Yahoo is a paper fallback, never a self-validating execution source.
        # Require an independent public exchange quote before allowing it through.
        consensus_pairs: list[tuple[Any, str, dict[str, Any]]] = []
        consensus_blocked: dict[str, list[str]] = defaultdict(list)
        for signal, symbol, oracle_quote in verified_pairs:
            if not _paper_yahoo_reference(oracle_quote):
                consensus_pairs.append((signal, symbol, oracle_quote))
                continue
            validation = _coinbase_reference_validation(symbol, oracle_quote.get("price"))
            if not validation.get("ok"):
                consensus_blocked[str(validation.get("reason") or "COINBASE_REFERENCE_REJECTED")].append(symbol)
                continue
            oracle_quote["price_consensus_verified"] = True
            oracle_quote["reference_provider"] = validation["reference_provider"]
            oracle_quote["reference_price"] = validation["reference_price"]
            oracle_quote["reference_timestamp"] = validation["reference_timestamp"]
            oracle_quote["reference_difference_pct"] = validation["difference_pct"]
            consensus_pairs.append((signal, symbol, oracle_quote))

        for reason, affected in consensus_blocked.items():
            worker.log.info(
                "CRYPTO | REFERENCE CONSENSUS SKIP | blocked=%d | reason=%s | sample=%s",
                len(affected),
                reason,
                ",".join(affected[:8]),
            )

        if not _live_execution_mode():
            return original_process_signals(
                market,
                [signal for signal, _, _ in consensus_pairs],
                quote_map,
                *args,
                **kwargs,
            )

        # Live mode: use Robinhood itself as the final executable-market truth.
        symbols = [symbol for _, symbol, _ in consensus_pairs]
        broker_quotes, broker_error = _broker_quote_map(symbols)
        if broker_error:
            if symbols:
                worker.log.warning(
                    "CRYPTO | LIVE BROKER QUOTE GATE | blocked=%d | reason=%s | sample=%s",
                    len(symbols),
                    broker_error[:180],
                    ",".join(symbols[:8]),
                )
            return original_process_signals(market, [], quote_map, *args, **kwargs)

        try:
            tolerance_pct = float(os.getenv("ROBINHOOD_BROKER_PRICE_TOLERANCE_PCT", "0.75"))
            max_spread_pct = float(os.getenv("ROBINHOOD_BROKER_MAX_SPREAD_PCT", "1.50"))
        except ValueError:
            tolerance_pct = 0.75
            max_spread_pct = 1.50

        from robinhood_crypto_api import validate_broker_market_reference

        executable: list[Any] = []
        blocked: dict[str, list[str]] = defaultdict(list)
        for signal, symbol, oracle_quote in consensus_pairs:
            broker_quote = broker_quotes.get(symbol)
            if broker_quote is None:
                blocked["BROKER_QUOTE_MISSING"].append(symbol)
                continue
            validation = validate_broker_market_reference(
                symbol,
                oracle_quote.get("price"),
                broker_quote,
                max_price_difference_pct=tolerance_pct,
                max_spread_pct=max_spread_pct,
            )
            if not validation.get("ok"):
                blocked[str(validation.get("reason") or "BROKER_QUOTE_REJECTED")].append(symbol)
                continue
            executable.append(signal)

        for reason, affected in blocked.items():
            worker.log.warning(
                "CRYPTO | LIVE BROKER QUOTE GATE | blocked=%d | reason=%s | sample=%s",
                len(affected),
                reason,
                ",".join(affected[:8]),
            )

        return original_process_signals(market, executable, quote_map, *args, **kwargs)

    worker.process_signals = guarded_process_signals
    worker._crypto_execution_quote_guard_installed = True
    log.info("Installed crypto provider, Coinbase-consensus, and live broker-market guards")
