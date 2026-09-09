from __future__ import annotations

import math
from typing import Any

from database import row, rows


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _count(query: str, params: tuple[Any, ...] = ()) -> int:
    result = row(query, params) or {}
    return int(result.get("count") or 0)


def paper_lifecycle_health(market: str = "crypto") -> dict[str, Any]:
    """Return read-only evidence that the paper entry/exit lifecycle has run.

    Prefer the explicit post-entry portfolio-reload event when it is still retained.
    Because global decision events are maintenance-pruned while paper orders/fills are
    durable accounting evidence, a completed BUY+SELL paper round trip also proves the
    entry lifecycle historically occurred. This diagnostic never creates trades and
    cannot authorize capital on its own.
    """
    normalized = str(market or "crypto").strip().lower()
    try:
        buy_orders = _count(
            """
            SELECT COUNT(*)::int AS count
            FROM paper_orders
            WHERE market=%s AND side='BUY'
              AND status IN ('FILLED','PARTIAL_CANCELLED')
              AND filled_quantity > 0 AND filled_notional > 0
            """,
            (normalized,),
        )
        sell_orders = _count(
            """
            SELECT COUNT(*)::int AS count
            FROM paper_orders
            WHERE market=%s AND side='SELL'
              AND status IN ('FILLED','PARTIAL_CANCELLED')
              AND filled_quantity > 0 AND filled_notional > 0
            """,
            (normalized,),
        )
        buy_fills = _count(
            "SELECT COUNT(*)::int AS count FROM paper_fills WHERE market=%s AND side='BUY' AND quantity > 0 AND notional > 0",
            (normalized,),
        )
        sell_fills = _count(
            "SELECT COUNT(*)::int AS count FROM paper_fills WHERE market=%s AND side='SELL' AND quantity > 0 AND notional > 0",
            (normalized,),
        )
        reload_events = _count(
            """
            SELECT COUNT(*)::int AS count
            FROM global_decision_events
            WHERE market=%s AND stage='paper_portfolio_reloaded'
            """,
            (normalized,),
        )
        held_positions = rows(
            """
            SELECT symbol, quantity, current_price
            FROM positions
            WHERE market=%s AND quantity > 0
            ORDER BY symbol
            """,
            (normalized,),
        )
        latest_buy = row(
            """
            SELECT order_id, symbol, status, filled_quantity, filled_notional, average_fill_price, fee_amount, created_at
            FROM paper_orders
            WHERE market=%s AND side='BUY' AND filled_quantity > 0
            ORDER BY id DESC LIMIT 1
            """,
            (normalized,),
        ) or {}
        latest_sell = row(
            """
            SELECT order_id, symbol, status, filled_quantity, filled_notional, average_fill_price, fee_amount, reason, created_at
            FROM paper_orders
            WHERE market=%s AND side='SELL' AND filled_quantity > 0
            ORDER BY id DESC LIMIT 1
            """,
            (normalized,),
        ) or {}

        durable_entry = buy_orders > 0 and buy_fills > 0
        exit_proven = sell_orders > 0 and sell_fills > 0
        durable_round_trip = durable_entry and exit_proven
        explicit_reload_proof = durable_entry and reload_events > 0
        entry_proven = explicit_reload_proof or durable_round_trip
        entry_proof_source = (
            "portfolio_reload_event"
            if explicit_reload_proof
            else "durable_round_trip_orders_fills"
            if durable_round_trip
            else "none"
        )
        round_trip_proven = entry_proven and exit_proven
        if round_trip_proven:
            status = "PASS"
        elif entry_proven:
            status = "ENTRY_PROVEN_EXIT_PENDING"
        else:
            status = "NO_PAPER_ENTRY_PROOF"

        held_market_value = sum(
            max(0.0, _finite(item.get("quantity"))) * max(0.0, _finite(item.get("current_price")))
            for item in held_positions
        )
        return {
            "ok": round_trip_proven,
            "status": status,
            "market": normalized,
            "paper_only_evidence": True,
            "entry_proven": entry_proven,
            "entry_proof_source": entry_proof_source,
            "explicit_reload_proof": explicit_reload_proof,
            "durable_round_trip_proof": durable_round_trip,
            "exit_proven": exit_proven,
            "round_trip_proven": round_trip_proven,
            "buy_orders": buy_orders,
            "sell_orders": sell_orders,
            "buy_fills": buy_fills,
            "sell_fills": sell_fills,
            "portfolio_reload_events": reload_events,
            "held_position_count": len(held_positions),
            "held_symbols": [str(item.get("symbol") or "").upper() for item in held_positions[:24]],
            "held_market_value": round(held_market_value, 8),
            "latest_buy": latest_buy,
            "latest_sell": latest_sell,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "UNAVAILABLE",
            "market": normalized,
            "paper_only_evidence": True,
            "entry_proven": False,
            "entry_proof_source": "none",
            "exit_proven": False,
            "round_trip_proven": False,
            "reason": exc.__class__.__name__,
        }
