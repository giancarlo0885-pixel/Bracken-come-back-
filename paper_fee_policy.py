from __future__ import annotations

from datetime import datetime, timezone
import logging
import math
import os
import uuid
from typing import Any


log = logging.getLogger("paper-fee-policy")


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _false_env(name: str) -> bool:
    return str(os.getenv(name, "false") or "false").strip().lower() in {"", "0", "false", "no", "off"}


def _paper_reconciliation_allowed() -> bool:
    return (
        str(os.getenv("EXECUTION_MODE", "") or "").strip().lower() == "paper"
        and _false_env("ENABLE_BROKER_SUBMISSION")
        and _false_env("LIVE_TRADING_ARMED")
    )


def _reconcile_partial_lot_gap(lots: list[Any], requested_quantity: float) -> None:
    """Backfill a historical paper-position lot gap from canonical cost basis.

    This runs only when the caller already has positive open lots but their total
    quantity is below the requested close.  The canonical ``positions`` row is
    treated as the source of truth.  A compensating lot is persisted only when
    its implied unit cost can be derived from canonical total cost minus the
    known open-lot cost.  No cash, trades, fills, or realized P&L are changed.
    """
    current_open = sum(max(0.0, _finite(getattr(lot, "quantity_remaining", 0.0))) for lot in lots)
    requested = max(0.0, _finite(requested_quantity))
    tolerance = max(1e-9, requested * 1e-9)
    if requested <= 0 or current_open + tolerance >= requested or not lots:
        return
    if not _paper_reconciliation_allowed():
        raise ValueError("paper lot reconciliation blocked by execution safety state")

    first = lots[0]
    symbol = str(getattr(first, "symbol", "") or "").upper().strip()
    market = str(getattr(first, "market", "") or "").lower().strip()
    if not symbol or not market:
        raise ValueError("paper lot reconciliation requires market and symbol")

    from database import connect
    from profit_attribution import PositionLot

    with connect() as repair_conn:
        position = repair_conn.execute(
            """
            SELECT * FROM positions
            WHERE market=%s AND symbol=%s
            LIMIT 1
            """,
            (market, symbol),
        ).fetchone()
        if not position:
            raise ValueError("paper lot reconciliation requires a canonical position")

        canonical_qty = max(0.0, _finite(position.get("quantity")))
        canonical_avg = _finite(position.get("average_price", position.get("entry_price", 0.0)))
        if canonical_qty + tolerance < requested or canonical_avg <= 0:
            raise ValueError("paper lot reconciliation canonical position is insufficient")

        db_lots = repair_conn.execute(
            """
            SELECT * FROM position_lots
            WHERE market=%s AND symbol=%s AND quantity_remaining > 0
            ORDER BY opened_at ASC, id ASC
            """,
            (market, symbol),
        ).fetchall()
        db_open = sum(max(0.0, _finite(row.get("quantity_remaining"))) for row in db_lots)
        if db_open + tolerance >= requested:
            raise ValueError("paper lot reconciliation detected concurrent lot state; retry close")

        missing_qty = canonical_qty - db_open
        if missing_qty <= tolerance:
            raise ValueError("paper lot reconciliation found no defensible missing quantity")

        known_cost = sum(
            max(0.0, _finite(row.get("quantity_remaining"))) * max(0.0, _finite(row.get("entry_price")))
            for row in db_lots
        )
        canonical_cost = canonical_qty * canonical_avg
        missing_cost = canonical_cost - known_cost
        implied_entry = missing_cost / missing_qty if missing_qty > 0 else 0.0
        if not math.isfinite(implied_entry) or implied_entry <= 0:
            raise ValueError("paper lot reconciliation could not derive a positive cost basis")

        now = datetime.now(timezone.utc)
        opened_at = position.get("opened_at") or position.get("updated_at") or now
        if not isinstance(opened_at, datetime):
            try:
                opened_at = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                opened_at = now
        if opened_at.tzinfo is None:
            opened_at = opened_at.replace(tzinfo=timezone.utc)

        lot_id = f"lot:reconcile:{market}:{symbol}:{uuid.uuid4()}"
        repair_conn.execute(
            """
            INSERT INTO position_lots (
                lot_id, symbol, market, bucket, strategy, opened_at,
                quantity_opened, quantity_remaining, entry_price, entry_fees,
                decision_id, broker_mode, account_environment, created_at
            )
            VALUES (%s,%s,%s,'Historical','canonical_lot_reconciliation',%s,%s,%s,%s,0,NULL,'PAPER','PAPER',%s)
            """,
            (lot_id, symbol, market, opened_at, missing_qty, missing_qty, implied_entry, now),
        )

    lots.append(
        PositionLot(
            lot_id=lot_id,
            symbol=symbol,
            market=market,
            bucket="Historical",
            strategy="canonical_lot_reconciliation",
            opened_at=opened_at,
            quantity_opened=missing_qty,
            quantity_remaining=missing_qty,
            entry_price=implied_entry,
            entry_fees=0.0,
            decision_id=None,
            broker_mode="PAPER",
            account_environment="PAPER",
        )
    )
    log.warning(
        "PAPER LOT RECONCILIATION | market=%s | symbol=%s | canonical_qty=%.10f | prior_open_qty=%.10f | backfilled_qty=%.10f | implied_entry=%.10f",
        market,
        symbol,
        canonical_qty,
        db_open,
        missing_qty,
        implied_entry,
    )


def fee_aware_fifo_close_lots(
    lots,
    *,
    quantity: float,
    exit_price: float,
    exit_time: Any,
    fees: float = 0.0,
    tier: str | None = None,
    confidence_score: float | None = None,
    weighted_signal_score: float | None = None,
    quote_provider: str | None = None,
    decision_id: str | None = None,
    order_id: str | None = None,
):
    """Close FIFO lots with round-trip fee-correct returns.

    Entry fees are already represented by the BUY ledger row and stored on the
    position lot. Therefore the SELL ledger row carries only its allocated exit
    fee in ``fees`` and ``net_pnl``. ``return_pct`` still includes both entry
    and exit fees, which keeps round-trip performance honest without counting
    the entry fee twice in portfolio-level ledger sums.
    """
    from profit_attribution import TradeLedgerRow

    remaining = _finite(quantity)
    if remaining <= 0:
        raise ValueError("close quantity must be positive")
    _reconcile_partial_lot_gap(lots, remaining)
    total_close_quantity = remaining
    # Position/lot quantities are persisted at finite decimal precision. Use the
    # same narrow tolerance for the terminal FIFO remainder so harmless numeric
    # dust cannot reject a fully attributable close. Material shortages still
    # exceed this bound and fail closed.
    quantity_tolerance = max(1e-10, total_close_quantity * 1e-9)
    exit_dt = exit_time
    if not isinstance(exit_dt, datetime):
        text = str(exit_time or "").strip()
        try:
            exit_dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            exit_dt = datetime.now(timezone.utc)
    if exit_dt.tzinfo is None:
        exit_dt = exit_dt.replace(tzinfo=timezone.utc)

    ordered = sorted(
        [lot for lot in lots if _finite(getattr(lot, "quantity_remaining", 0.0)) > 0],
        key=lambda lot: lot.opened_at,
    )
    rows = []
    total_exit_fee = max(0.0, _finite(fees))
    for lot in ordered:
        if remaining <= 0:
            break
        lot_remaining = _finite(lot.quantity_remaining)
        close_qty = min(lot_remaining, remaining)
        if close_qty <= 0:
            continue

        close_fraction = close_qty / total_close_quantity
        exit_fee_allocated = total_exit_fee * close_fraction
        opened_qty = max(_finite(lot.quantity_opened), close_qty)
        entry_fee_allocated = max(0.0, _finite(lot.entry_fees)) * (close_qty / opened_qty)
        gross = (_finite(exit_price) - _finite(lot.entry_price)) * close_qty
        sell_net = gross - exit_fee_allocated
        round_trip_net = gross - entry_fee_allocated - exit_fee_allocated
        entry_cost_basis = _finite(lot.entry_price) * close_qty + entry_fee_allocated
        return_pct = round_trip_net / entry_cost_basis * 100.0 if entry_cost_basis > 0 else 0.0

        lot_after = round(lot_remaining - close_qty, 10)
        remaining_after = round(remaining - close_qty, 10)
        lot.quantity_remaining = 0.0 if abs(lot_after) <= quantity_tolerance else lot_after
        remaining = 0.0 if abs(remaining_after) <= quantity_tolerance else remaining_after
        rows.append(
            TradeLedgerRow(
                trade_id=str(uuid.uuid4()),
                symbol=lot.symbol,
                market=lot.market,
                bucket=lot.bucket,
                strategy=lot.strategy,
                side="SELL",
                quantity=close_qty,
                entry_time=lot.opened_at,
                entry_price=lot.entry_price,
                exit_time=exit_dt,
                exit_price=_finite(exit_price),
                gross_pnl=round(gross, 10),
                fees=round(exit_fee_allocated, 10),
                net_pnl=round(sell_net, 10),
                return_pct=round(return_pct, 10),
                tier=tier,
                confidence_score=confidence_score,
                weighted_signal_score=weighted_signal_score,
                quote_provider=quote_provider,
                decision_id=decision_id if decision_id is not None else lot.decision_id,
                status="CLOSED" if lot.quantity_remaining <= 0 else "PARTIAL",
                broker_mode=lot.broker_mode,
                account_environment=lot.account_environment,
                order_id=order_id,
            )
        )

    if remaining > quantity_tolerance:
        raise ValueError("not enough open lot quantity to close")
    return rows


def install_fee_aware_fifo_policy() -> None:
    """Install the fee-correct FIFO function into runtime execution modules."""
    import oracle_bot
    import profit_attribution

    oracle_bot.fifo_close_lots = fee_aware_fifo_close_lots
    profit_attribution.fifo_close_lots = fee_aware_fifo_close_lots