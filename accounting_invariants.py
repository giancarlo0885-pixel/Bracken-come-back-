from __future__ import annotations

from dataclasses import dataclass, asdict
import math
from typing import Any

from database import row


@dataclass(frozen=True)
class MarketAccountingInvariant:
    market: str
    ok: bool
    cash: float
    positions_value: float
    margin_debt: float
    margin_interest: float
    computed_equity: float
    snapshot_equity: float | None
    equity_difference: float | None
    invalid_positions: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def equity_equation(
    *,
    cash: float,
    positions_value: float,
    margin_debt: float = 0.0,
    margin_interest: float = 0.0,
) -> float:
    return _finite(cash) + _finite(positions_value) - max(0.0, _finite(margin_debt)) - max(0.0, _finite(margin_interest))


def _market_invariant(market: str, tolerance_pct: float) -> MarketAccountingInvariant:
    portfolio = row(
        """
        SELECT cash, margin_debt, margin_interest_accrued
        FROM portfolios WHERE market=%s
        """,
        (market,),
    ) or {}
    position_stats = row(
        """
        SELECT
            COALESCE(SUM(quantity * current_price),0) AS positions_value,
            COUNT(*) FILTER (
                WHERE quantity < -1e-10
                   OR current_price <= 0
                   OR quantity IS NULL
                   OR current_price IS NULL
            )::int AS invalid_positions
        FROM positions WHERE market=%s
        """,
        (market,),
    ) or {}
    snapshot = row(
        """
        SELECT equity, cash, positions_value, created_at
        FROM equity_snapshots WHERE market=%s
        ORDER BY id DESC LIMIT 1
        """,
        (market,),
    ) or {}

    cash = _finite(portfolio.get("cash"))
    positions_value = _finite(position_stats.get("positions_value"))
    debt = max(0.0, _finite(portfolio.get("margin_debt")))
    interest = max(0.0, _finite(portfolio.get("margin_interest_accrued")))
    computed = equity_equation(
        cash=cash,
        positions_value=positions_value,
        margin_debt=debt,
        margin_interest=interest,
    )
    snapshot_equity = None if snapshot.get("equity") is None else _finite(snapshot.get("equity"))
    difference = None if snapshot_equity is None else abs(snapshot_equity - computed)
    tolerance = max(0.05, abs(computed) * max(0.0, tolerance_pct))
    ok = int(position_stats.get("invalid_positions") or 0) == 0 and snapshot_equity is not None and difference is not None and difference <= tolerance
    return MarketAccountingInvariant(
        market=market,
        ok=ok,
        cash=cash,
        positions_value=positions_value,
        margin_debt=debt,
        margin_interest=interest,
        computed_equity=computed,
        snapshot_equity=snapshot_equity,
        equity_difference=difference,
        invalid_positions=int(position_stats.get("invalid_positions") or 0),
    )


def accounting_health(*, tolerance_pct: float = 0.001) -> dict[str, Any]:
    """Read-only accounting reconciliation across canonical paper records."""
    try:
        markets = [_market_invariant("cash", tolerance_pct), _market_invariant("crypto", tolerance_pct)]
        order_mismatch = row(
            """
            SELECT COUNT(*)::int AS count
            FROM (
                SELECT o.order_id
                FROM paper_orders o
                LEFT JOIN paper_fills f ON f.order_id=o.order_id
                GROUP BY o.order_id, o.filled_quantity, o.fee_amount
                HAVING ABS(COALESCE(o.filled_quantity,0)-COALESCE(SUM(f.quantity),0)) > 1e-8
                    OR ABS(COALESCE(o.fee_amount,0)-COALESCE(SUM(f.fee_amount),0)) > 0.01
            ) q
            """
        ) or {"count": 0}
        lot_mismatch = row(
            """
            SELECT COUNT(*)::int AS count
            FROM position_lots
            WHERE quantity_opened < -1e-10
               OR quantity_remaining < -1e-10
               OR quantity_remaining - quantity_opened > 1e-8
               OR entry_price <= 0
               OR entry_fees < -1e-10
            """
        ) or {"count": 0}
        ledger_mismatch = row(
            """
            SELECT COUNT(*)::int AS count
            FROM trade_ledger
            WHERE ABS(COALESCE(net_pnl,0) - (COALESCE(gross_pnl,0)-COALESCE(fees,0))) > 0.01
               OR quantity <= 0
               OR COALESCE(fees,0) < -1e-10
            """
        ) or {"count": 0}
        execution_invalid = row(
            """
            SELECT COUNT(*)::int AS count
            FROM executions
            WHERE quantity <= 0 OR fill_price <= 0 OR COALESCE(fees,0) < -1e-10
            """
        ) or {"count": 0}
        mismatches = {
            "paper_order_fill_mismatches": int(order_mismatch.get("count") or 0),
            "position_lot_mismatches": int(lot_mismatch.get("count") or 0),
            "trade_ledger_mismatches": int(ledger_mismatch.get("count") or 0),
            "invalid_executions": int(execution_invalid.get("count") or 0),
        }
        ok = all(item.ok for item in markets) and all(value == 0 for value in mismatches.values())
        return {
            "ok": ok,
            "status": "PASS" if ok else "FAIL_CLOSED",
            "markets": [item.to_dict() for item in markets],
            **mismatches,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "UNAVAILABLE",
            "reason": exc.__class__.__name__,
            "markets": [],
        }
