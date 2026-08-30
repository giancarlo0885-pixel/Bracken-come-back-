from __future__ import annotations

import logging
import os
from collections import defaultdict
from typing import Any

log = logging.getLogger("crypto-execution-guard")


def _symbol(signal: Any) -> str:
    if isinstance(signal, dict):
        value = signal.get("symbol")
    else:
        value = getattr(signal, "symbol", None)
    return str(value or "").upper().strip()


def _live_execution_mode() -> bool:
    return str(os.getenv("EXECUTION_MODE", "paper") or "paper").lower().strip() == "live"


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
    """Filter crypto signals without verified quotes before Oracle processing.

    Paper mode requires the Oracle's verified quote exactly as before. Future
    live mode adds a second fail-closed gate: Robinhood must return a valid best
    bid/ask for the same symbol and its midpoint must agree with the Oracle mark
    within the configured tolerance. This guard never submits an order.
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

        if not _live_execution_mode():
            return original_process_signals(
                market,
                [signal for signal, _, _ in verified_pairs],
                quote_map,
                *args,
                **kwargs,
            )

        # Live mode: use Robinhood itself as the final executable-market truth.
        symbols = [symbol for _, symbol, _ in verified_pairs]
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
        for signal, symbol, oracle_quote in verified_pairs:
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
    log.info("Installed crypto verified-quote and live broker-market guard")
