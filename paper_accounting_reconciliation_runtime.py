from __future__ import annotations

import logging
import math
import os
from typing import Any

from accounting_invariants import accounting_health
from database import row


log = logging.getLogger("crypto-worker")


def _truthy(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default) or default).strip().lower() == "true"


def _paper_only() -> bool:
    return (
        str(os.getenv("EXECUTION_MODE", "paper") or "paper").strip().lower() == "paper"
        and not _truthy("ENABLE_BROKER_SUBMISSION")
        and not _truthy("LIVE_TRADING_ARMED")
    )


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _money(value: Any) -> float:
    """Normalize persisted paper money arithmetic without hiding real differences."""
    return round(_finite(value), 10)


def emit_paper_accounting_reconciliation(market: str = "crypto") -> dict[str, Any]:
    """Emit SELECT-only P&L/equity diagnostics for the canonical paper account.

    This never repairs balances. It explains whether the change from starting
    capital is supported by persisted realized P&L, open-position P&L, and the
    entry-side fees that reduce cash before a position is closed.
    """
    if not _paper_only():
        return {"ok": False, "status": "DISABLED", "reason": "live_not_disarmed"}

    market = str(market or "crypto").strip().lower()
    try:
        portfolio = row(
            """
            SELECT cash, starting_balance, margin_debt, margin_interest_accrued
            FROM portfolios WHERE market=%s
            """,
            (market,),
        ) or {}
        positions = row(
            """
            SELECT
                COALESCE(SUM(quantity * current_price),0) AS positions_value,
                COALESCE(SUM(quantity * (current_price - average_price)),0) AS open_unrealized_pnl,
                COUNT(*)::int AS open_positions
            FROM positions WHERE market=%s
            """,
            (market,),
        ) or {}
        sells = row(
            """
            SELECT
                COUNT(*)::int AS sell_count,
                COALESCE(SUM(realized_pnl),0) AS net_realized_pnl,
                COALESCE(SUM(gross_realized_pnl),0) AS gross_realized_pnl,
                COALESCE(SUM(fees),0) AS sell_fees
            FROM trades
            WHERE market=%s AND side='SELL'
            """,
            (market,),
        ) or {}
        fills = row(
            """
            SELECT
                COUNT(*)::int AS fill_count,
                COALESCE(SUM(fee_amount),0) AS all_fill_fees,
                COALESCE(SUM(CASE WHEN side='BUY' THEN fee_amount ELSE 0 END),0) AS buy_fill_fees,
                COALESCE(SUM(CASE WHEN side='SELL' THEN fee_amount ELSE 0 END),0) AS sell_fill_fees
            FROM paper_fills
            WHERE market=%s
            """,
            (market,),
        ) or {}

        cash = _money(portfolio.get("cash"))
        start = _money(portfolio.get("starting_balance"))
        position_value = _money(positions.get("positions_value"))
        debt = max(0.0, _money(portfolio.get("margin_debt")))
        interest = max(0.0, _money(portfolio.get("margin_interest_accrued")))
        canonical_equity = _money(cash + position_value - debt - interest)
        equity_change = _money(canonical_equity - start)
        realized = _money(sells.get("net_realized_pnl"))
        unrealized = _money(positions.get("open_unrealized_pnl"))
        buy_fees = _money(fills.get("buy_fill_fees"))
        # SELL realized_pnl is already net of exit fees. BUY-side fill fees are
        # a separate cash reduction and must be included once in the equity bridge.
        explained_pnl = _money(realized + unrealized - buy_fees)
        residual = _money(equity_change - explained_pnl)
        tolerance = max(0.50, abs(start) * 0.0025)
        pnl_status = "EXPLAINED" if abs(residual) <= tolerance else "REVIEW"

        health = accounting_health()
        market_health = next(
            (item for item in health.get("markets", []) if item.get("market") == market),
            {},
        )
        invariant_ok = bool(market_health.get("ok"))
        status = "PASS" if invariant_ok and pnl_status == "EXPLAINED" else "REVIEW"
        payload = {
            "ok": status == "PASS",
            "status": status,
            "market": market,
            "starting_balance": start,
            "cash": cash,
            "positions_value": position_value,
            "canonical_equity": canonical_equity,
            "equity_change": equity_change,
            "net_realized_pnl": realized,
            "gross_realized_pnl": _money(sells.get("gross_realized_pnl")),
            "open_unrealized_pnl": unrealized,
            "explained_pnl": explained_pnl,
            "diagnostic_residual": residual,
            "sell_fees": _money(sells.get("sell_fees")),
            "all_fill_fees": _money(fills.get("all_fill_fees")),
            "buy_fill_fees": buy_fees,
            "sell_fill_fees": _money(fills.get("sell_fill_fees")),
            "sell_count": int(sells.get("sell_count") or 0),
            "fill_count": int(fills.get("fill_count") or 0),
            "open_positions": int(positions.get("open_positions") or 0),
            "pnl_status": pnl_status,
            "accounting_invariant_ok": invariant_ok,
            "order_fill_mismatches": int(health.get("paper_order_fill_mismatches") or 0),
            "lot_mismatches": int(health.get("position_lot_mismatches") or 0),
            "ledger_mismatches": int(health.get("trade_ledger_mismatches") or 0),
            "invalid_executions": int(health.get("invalid_executions") or 0),
        }
        log.info(
            "PAPER ACCOUNTING RECONCILIATION | status=%s | market=%s | start=%.6f | cash=%.6f | "
            "positions=%.6f | equity=%.6f | equity_change=%.6f | realized=%.6f | unrealized=%.6f | "
            "buy_fees=%.6f | explained=%.6f | residual=%.6f | sell_fees=%.6f | all_fill_fees=%.6f | "
            "invariant_ok=%s | order_fill_mismatches=%d | lot_mismatches=%d | ledger_mismatches=%d | "
            "invalid_executions=%d | broker_submission=NONE | live_trading=DISARMED",
            status, market, start, cash, position_value, canonical_equity, equity_change,
            realized, unrealized, buy_fees, explained_pnl, residual, payload["sell_fees"], payload["all_fill_fees"],
            invariant_ok, payload["order_fill_mismatches"], payload["lot_mismatches"],
            payload["ledger_mismatches"], payload["invalid_executions"],
        )
        return payload
    except Exception as exc:
        log.warning(
            "PAPER ACCOUNTING RECONCILIATION | status=UNAVAILABLE | market=%s | error=%s | "
            "broker_submission=NONE | live_trading=DISARMED",
            market,
            exc.__class__.__name__,
        )
        return {"ok": False, "status": "UNAVAILABLE", "reason": exc.__class__.__name__}
