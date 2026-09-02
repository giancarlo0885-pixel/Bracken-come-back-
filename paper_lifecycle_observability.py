from __future__ import annotations

import logging
import math
import os
from typing import Any


log = logging.getLogger("paper-lifecycle")
_ENTRY_ACTIONS = {"BUY", "STRONG_BUY", "ACCUMULATE", "LONG"}
_EXIT_ACTIONS = {"SELL", "CLOSE", "EXIT"}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _held_positions(positions: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    held: list[dict[str, Any]] = []
    for item in positions or []:
        quantity = _finite(item.get("quantity"))
        if quantity > 0:
            held.append(item)
    return held


def install_paper_lifecycle_observability(worker: Any) -> None:
    """Prove post-execution portfolio reload without changing trading decisions.

    The wrapper is installed only in paper mode. It runs after the existing V39
    executor returns and therefore cannot authorize, size, or submit an order.
    It immediately reloads the canonical portfolio/positions and records a
    sanitized decision event for each executed action.
    """
    if str(os.getenv("EXECUTION_MODE", "paper") or "paper").strip().lower() != "paper":
        log.info("Paper lifecycle observability not installed outside paper mode")
        return
    if getattr(worker, "_paper_lifecycle_observability_installed", False):
        return

    original = getattr(worker, "_v39_execute_iterative", None)
    if not callable(original):
        raise RuntimeError("market worker has no _v39_execute_iterative to observe")

    def observed_execute_iterative(
        market: str,
        signals: list[Any],
        prices: dict[str, Any],
        ranked: list[dict[str, Any]],
        scan_type: str,
    ) -> list[Any]:
        actions = original(market, signals, prices, ranked, scan_type) or []
        executed = [item for item in actions if isinstance(item, dict) and str(item.get("action") or "").upper() in (_ENTRY_ACTIONS | _EXIT_ACTIONS)]
        if not executed:
            return actions

        try:
            portfolio, positions = worker._v39_position_rows(market)
        except Exception as exc:
            log.warning(
                "PAPER_PORTFOLIO_RELOAD | market=%s | executed_actions=%d | status=FAILED | reason=%s",
                market,
                len(executed),
                exc.__class__.__name__,
            )
            return actions

        held = _held_positions(positions)
        held_symbols = [str(item.get("symbol") or "").upper() for item in held if item.get("symbol")]
        buy_count = sum(1 for item in executed if str(item.get("action") or "").upper() in _ENTRY_ACTIONS)
        sell_count = sum(1 for item in executed if str(item.get("action") or "").upper() in _EXIT_ACTIONS)
        cash = _finite(portfolio.get("cash"))
        equity = _finite(portfolio.get("equity") or portfolio.get("total_equity"))

        log.info(
            "PAPER_PORTFOLIO_RELOAD | market=%s | executed_actions=%d | buys=%d | sells=%d | held=%d | held_symbols=%s | cash=%.2f | equity=%.2f | mode=paper | status=PASS",
            market,
            len(executed),
            buy_count,
            sell_count,
            len(held),
            held_symbols[:12],
            cash,
            equity,
        )

        recorder = getattr(worker, "_v39_record_event", None)
        if callable(recorder):
            for item in executed:
                symbol = str(item.get("symbol") or "").upper()
                if not symbol:
                    continue
                approved_amount = _finite(item.get("optimizer_approved_amount") or item.get("planned_trade_value"))
                payload = {
                    "market": str(market or "").lower(),
                    "symbol": symbol,
                    "action": str(item.get("action") or "").upper(),
                    "trade_id": item.get("trade_id"),
                    "signal_id": item.get("signal_id"),
                    "forecast_id": item.get("forecast_id"),
                    "optimizer_approved_amount": approved_amount,
                    "portfolio_reloaded": True,
                    "held_position_count": len(held),
                    "held_symbols": held_symbols[:24],
                    "cash": cash,
                    "equity": equity,
                    "execution_mode": "paper",
                    "scan_type": scan_type,
                }
                try:
                    recorder(market, symbol, "paper_portfolio_reloaded", payload)
                except Exception as exc:
                    log.debug("Paper lifecycle decision event skipped | symbol=%s | error=%s", symbol, exc)
        return actions

    worker._v39_execute_iterative = observed_execute_iterative
    worker._paper_lifecycle_observability_installed = True
    log.info("Installed paper lifecycle portfolio-reload observability")
