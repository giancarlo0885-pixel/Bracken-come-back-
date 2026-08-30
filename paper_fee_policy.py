from __future__ import annotations

from datetime import datetime, timezone
import math
import uuid
from typing import Any


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


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
    position lot.  Therefore the SELL ledger row carries only its allocated exit
    fee in ``fees`` and ``net_pnl``.  ``return_pct`` still includes both entry
    and exit fees, which keeps round-trip performance honest without counting
    the entry fee twice in portfolio-level ledger sums.
    """
    from profit_attribution import TradeLedgerRow

    remaining = _finite(quantity)
    if remaining <= 0:
        raise ValueError("close quantity must be positive")
    total_close_quantity = remaining
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

        lot.quantity_remaining = round(lot_remaining - close_qty, 10)
        remaining = round(remaining - close_qty, 10)
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

    if remaining > 0:
        raise ValueError("not enough open lot quantity to close")
    return rows


def install_fee_aware_fifo_policy() -> None:
    """Install the fee-correct FIFO function into runtime execution modules."""
    import oracle_bot
    import profit_attribution

    oracle_bot.fifo_close_lots = fee_aware_fifo_close_lots
    profit_attribution.fifo_close_lots = fee_aware_fifo_close_lots
