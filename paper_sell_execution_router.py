from __future__ import annotations

import logging
import os
from typing import Any


log = logging.getLogger("paper-sell-router")
_INSTALLED = False


def _truthy(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default) or default).strip().lower() == "true"


def _active() -> bool:
    return (
        str(os.getenv("EXECUTION_MODE", "paper") or "paper").strip().lower() == "paper"
        and _truthy("PAPER_AUTONOMOUS_LEARNING")
        and not _truthy("ENABLE_BROKER_SUBMISSION")
        and not _truthy("LIVE_TRADING_ARMED")
    )


def _value(signal: Any, name: str, default: Any = None) -> Any:
    if isinstance(signal, dict):
        return signal.get(name, default)
    return getattr(signal, name, default)


def install_paper_sell_execution_router(worker: Any) -> bool:
    """Route autonomous paper SELL decisions through the canonical exit path.

    V39's iterative executor is intentionally entry-oriented and normally drops
    non-entry actions while entries are enabled. For autonomous crypto paper
    learning that starves the exit lifecycle even when the model emits SELL.

    This wrapper executes SELL only for symbols that are already held and only
    through the existing ``process_signals`` path, so quote verification, broker
    reference validation, fill realism, persistence, FIFO/realized-P&L accounting,
    and portfolio reload logic remain authoritative. It is impossible to activate
    while broker submission or live trading is armed.
    """
    global _INSTALLED
    if _INSTALLED:
        return True
    if not _active():
        return False

    original = worker._v39_execute_iterative
    if not callable(original):
        raise RuntimeError("market worker has no _v39_execute_iterative")

    def paper_exit_first(
        market: str,
        signals: list[Any],
        prices: dict[str, Any],
        ranked: list[dict[str, Any]],
        scan_type: str,
    ) -> list[Any]:
        if not (_active() and str(market or "").strip().lower() == "crypto"):
            return original(market, signals, prices, ranked, scan_type)

        signal_list = list(signals or [])
        if not signal_list:
            return original(market, signal_list, prices, ranked, scan_type)

        try:
            _, positions = worker._v39_position_rows(market)
        except Exception as exc:
            log.warning("PAPER SELL ROUTER | position_load=FAIL | error=%s", exc.__class__.__name__)
            return original(market, signal_list, prices, ranked, scan_type)

        held = {
            str((position.get("symbol") if isinstance(position, dict) else getattr(position, "symbol", "")) or "").upper().strip()
            for position in positions or []
        }
        held.discard("")

        sell_actions = {"SELL", "STRONG_SELL", "STRONG SELL", "EXIT", "CLOSE", "REDUCE"}
        routed: list[Any] = []
        seen: set[str] = set()
        remaining: list[Any] = []

        for signal in signal_list:
            symbol = str(_value(signal, "symbol", "") or "").upper().strip()
            action = str(_value(signal, "action", "") or "").upper().strip()
            is_sell = action in sell_actions
            if not is_sell:
                remaining.append(signal)
                continue
            if not symbol or symbol not in held or symbol in seen:
                # Never create a short/naked paper position and never duplicate an
                # exit for the same symbol in one optimizer cycle.
                continue
            quote = dict((prices or {}).get(symbol) or {})
            eligible = getattr(worker, "_execution_quote_eligible", None)
            if callable(eligible) and not eligible(quote):
                log.info("PAPER SELL ROUTER | symbol=%s | action=%s | routed=False | reason=quote_not_eligible", symbol, action)
                continue
            seen.add(symbol)
            routed.append(signal)

        actions: list[Any] = []
        if routed:
            symbols = ",".join(str(_value(item, "symbol", "") or "").upper() for item in routed)
            log.info(
                "PAPER SELL ROUTER | scan=%s | held=%d | routed=%d | symbols=%s | broker_submission=NONE | live_trading=DISARMED",
                scan_type,
                len(held),
                len(routed),
                symbols,
            )
            try:
                actions.extend(worker.process_signals(market, routed, prices=prices) or [])
            except Exception:
                log.exception("PAPER SELL ROUTER | execution=FAIL | symbols=%s", symbols)

        # Entry optimization continues independently after exits. SELL signals are
        # removed so the entry-only V39 loop cannot silently discard/reprocess them.
        if remaining:
            actions.extend(original(market, remaining, prices, ranked, scan_type) or [])
        return actions

    paper_exit_first._oracle_paper_sell_router = True
    worker._v39_execute_iterative = paper_exit_first
    _INSTALLED = True
    log.info("Installed autonomous paper SELL router | broker_submission=NONE | live_trading=DISARMED")
    return True
