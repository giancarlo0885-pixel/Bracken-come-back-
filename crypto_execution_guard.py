from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("crypto-execution-guard")


def _symbol(signal: Any) -> str:
    if isinstance(signal, dict):
        value = signal.get("symbol")
    else:
        value = getattr(signal, "symbol", None)
    return str(value or "").upper().strip()


def install_crypto_execution_quote_guard(worker: Any) -> None:
    """Filter crypto signals without verified quotes before Oracle processing.

    The trading engine already rejects unverified quotes, but historically those
    signals reached ``process_signals`` first and were rendered/logged with a
    synthetic 0.00 price. This guard fails closed earlier without changing quote
    verification, strategy thresholds, or provider routing.
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
        executable: list[Any] = []
        skipped: list[str] = []
        for signal in list(signals or []):
            symbol = _symbol(signal)
            if not symbol:
                continue
            quote = oracle_bot._verified_quote_for(symbol, quote_map, "crypto")
            if quote is None:
                skipped.append(symbol)
                continue
            executable.append(signal)

        if skipped:
            worker.log.info(
                "CRYPTO | EXECUTION SKIP | no verified live quote | affected_symbols=%d | sample=%s",
                len(skipped),
                ",".join(skipped[:8]),
            )

        return original_process_signals(market, executable, quote_map, *args, **kwargs)

    worker.process_signals = guarded_process_signals
    worker._crypto_execution_quote_guard_installed = True
    log.info("Installed crypto unverified-quote execution guard")
