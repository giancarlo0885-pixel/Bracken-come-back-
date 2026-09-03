from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import os
from typing import Any


log = logging.getLogger("paper-sell-db-verification")


def _compact(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    allowed = {
        "id", "market", "symbol", "side", "quantity", "price", "value",
        "realized_pnl", "gross_realized_pnl", "fees", "reason", "created_at",
        "order_id", "status", "requested_quantity", "requested_notional",
        "filled_quantity", "filled_notional", "reference_price",
        "average_fill_price", "fee_amount", "fill_id", "fill_price", "notional",
        "slippage_pct", "spread_pct", "market_impact_pct", "quote_provider",
        "cash", "equity", "total_equity", "average_price", "cost_basis",
        "remaining_quantity", "entry_price", "opened_at", "closed_at",
    }
    return {key: row.get(key) for key in allowed if key in row}


def _verbose() -> bool:
    return str(os.getenv("PAPER_SELL_DB_VERIFY_VERBOSE", "false") or "false").strip().lower() == "true"


def _explicit_cutoff(default_cutoff: str) -> str:
    value = str(os.getenv("PAPER_SELL_VERIFY_AFTER", "") or "").strip()
    return value or default_cutoff


def _first(rows_fn: Any, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
    found = rows_fn(sql, params) or []
    return dict(found[0]) if found else None


def _emit_exact_first_sell(rows_fn: Any, cutoff: str) -> dict[str, Any]:
    """SELECT-only proof for the first crypto SELL at/after cutoff."""
    trade = _first(
        rows_fn,
        """
        SELECT id, market, symbol, side, quantity, price, value,
               realized_pnl, gross_realized_pnl, fees, reason, created_at
        FROM trades
        WHERE market=%s AND side='SELL' AND created_at >= %s
        ORDER BY created_at ASC
        LIMIT 1
        """,
        ("crypto", cutoff),
    )
    if not trade:
        log.info("PAPER SELL EXACT VERIFY | status=WAITING | cutoff=%s | reason=no_post_cutoff_sell", cutoff)
        return {"ok": False, "reason": "no_post_cutoff_sell"}

    symbol = str(trade.get("symbol") or "").upper()
    created_at = trade.get("created_at")
    order = _first(
        rows_fn,
        """
        SELECT id, order_id, market, symbol, side, status,
               requested_quantity, requested_notional, filled_quantity,
               filled_notional, reference_price, average_fill_price,
               fee_amount, quote_provider, reason, created_at
        FROM paper_orders
        WHERE market=%s AND side='SELL' AND symbol=%s AND created_at >= %s
        ORDER BY created_at ASC
        LIMIT 1
        """,
        ("crypto", symbol, created_at),
    )
    fill = None
    if order and order.get("order_id"):
        fill = _first(
            rows_fn,
            """
            SELECT id, fill_id, order_id, market, symbol, side, quantity,
                   reference_price, fill_price, notional, fee_amount,
                   slippage_pct, spread_pct, market_impact_pct,
                   quote_provider, created_at
            FROM paper_fills
            WHERE market=%s AND side='SELL' AND order_id=%s
            ORDER BY created_at ASC
            LIMIT 1
            """,
            ("crypto", order.get("order_id")),
        )

    position = _first(
        rows_fn,
        "SELECT * FROM positions WHERE market=%s AND symbol=%s LIMIT 1",
        ("crypto", symbol),
    )
    portfolio = _first(
        rows_fn,
        "SELECT * FROM portfolios WHERE market=%s LIMIT 1",
        ("crypto",),
    )

    realized = trade.get("realized_pnl")
    realized_recorded = realized is not None
    order_filled = bool(order and str(order.get("status") or "").upper() == "FILLED")
    fill_recorded = bool(fill)
    trade_qty = float(trade.get("quantity") or 0.0)
    fill_qty = float((fill or {}).get("quantity") or 0.0)
    quantity_match = bool(fill_recorded and abs(trade_qty - fill_qty) <= max(1e-10, abs(trade_qty) * 1e-6))
    current_qty = float((position or {}).get("quantity") or 0.0)

    status = "PASS" if realized_recorded and order_filled and fill_recorded and quantity_match else "FAIL"
    log.info(
        "PAPER SELL EXACT VERIFY | status=%s | cutoff=%s | symbol=%s | trade_id=%s | order_id=%s | fill_id=%s | trade_qty=%.12f | fill_qty=%.12f | quantity_match=%s | realized_pnl=%s | realized_recorded=%s | current_position_qty=%.12f | cash=%s | equity=%s | mode=paper",
        status,
        cutoff,
        symbol,
        trade.get("id"),
        (order or {}).get("order_id"),
        (fill or {}).get("fill_id"),
        trade_qty,
        fill_qty,
        quantity_match,
        realized,
        realized_recorded,
        current_qty,
        (portfolio or {}).get("cash"),
        (portfolio or {}).get("equity") or (portfolio or {}).get("total_equity"),
    )
    log.info("PAPER SELL EXACT TRADE | %s", _compact(trade))
    log.info("PAPER SELL EXACT ORDER | %s", _compact(order))
    log.info("PAPER SELL EXACT FILL | %s", _compact(fill))
    log.info("PAPER SELL EXACT POSITION | %s", _compact(position))
    log.info("PAPER SELL EXACT PORTFOLIO | %s", _compact(portfolio))

    return {
        "ok": status == "PASS",
        "trade": trade,
        "order": order,
        "fill": fill,
        "position": position,
        "portfolio": portfolio,
        "quantity_match": quantity_match,
        "realized_recorded": realized_recorded,
    }


def emit_recent_crypto_sell_db_verification(*, lookback_minutes: int = 180) -> dict[str, Any]:
    """Read and summarize persisted crypto paper SELL evidence from Postgres.

    SELECT queries only. If PAPER_SELL_VERIFY_AFTER is set, an exact first-SELL
    proof is emitted in addition to the rolling summary.
    """
    from database import rows

    default_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max(1, lookback_minutes))).isoformat()
    cutoff = _explicit_cutoff(default_cutoff)

    try:
        exact = _emit_exact_first_sell(rows, cutoff)
    except Exception as exc:
        log.warning("PAPER SELL EXACT VERIFY | status=UNAVAILABLE | error=%s", exc.__class__.__name__)
        exact = {"ok": False, "reason": f"exact:{exc.__class__.__name__}"}

    try:
        trades = rows(
            """
            SELECT id, market, symbol, side, quantity, price, value,
                   realized_pnl, gross_realized_pnl, fees, reason, created_at
            FROM trades
            WHERE market=%s AND side='SELL' AND created_at >= %s
            ORDER BY created_at ASC
            LIMIT 20
            """,
            ("crypto", cutoff),
        ) or []
    except Exception as exc:
        log.warning("PAPER SELL DB VERIFY | trades=UNAVAILABLE | error=%s", exc.__class__.__name__)
        return {"ok": False, "reason": f"trades:{exc.__class__.__name__}", "exact": exact}

    symbols = sorted({str(item.get("symbol") or "").upper() for item in trades if item.get("symbol")})
    orders: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    if symbols:
        placeholders = ",".join(["%s"] * len(symbols))
        try:
            orders = rows(
                f"""
                SELECT id, order_id, market, symbol, side, status,
                       requested_quantity, requested_notional, filled_quantity,
                       filled_notional, reference_price, average_fill_price,
                       fee_amount, quote_provider, reason, created_at
                FROM paper_orders
                WHERE market=%s AND side='SELL' AND symbol IN ({placeholders}) AND created_at >= %s
                ORDER BY created_at ASC
                LIMIT 40
                """,
                tuple(["crypto", *symbols, cutoff]),
            ) or []
        except Exception as exc:
            log.warning("PAPER SELL DB VERIFY | orders=UNAVAILABLE | error=%s", exc.__class__.__name__)
        try:
            fills = rows(
                f"""
                SELECT id, fill_id, order_id, market, symbol, side, quantity,
                       reference_price, fill_price, notional, fee_amount,
                       slippage_pct, spread_pct, market_impact_pct,
                       quote_provider, created_at
                FROM paper_fills
                WHERE market=%s AND side='SELL' AND symbol IN ({placeholders}) AND created_at >= %s
                ORDER BY created_at ASC
                LIMIT 40
                """,
                tuple(["crypto", *symbols, cutoff]),
            ) or []
        except Exception as exc:
            log.warning("PAPER SELL DB VERIFY | fills=UNAVAILABLE | error=%s", exc.__class__.__name__)

    net_realized = sum(float(item.get("realized_pnl") or 0.0) for item in trades)
    gross_realized = sum(float(item.get("gross_realized_pnl") or 0.0) for item in trades)
    fees = sum(float(item.get("fees") or 0.0) for item in trades)
    log.info(
        "PAPER SELL DB VERIFY | status=%s | sells=%d | orders=%d | fills=%d | symbols=%s | net_realized_pnl=%.8f | gross_realized_pnl=%.8f | fees=%.8f | exact=%s | verbose=%s",
        "PASS" if trades and fills else "PARTIAL",
        len(trades), len(orders), len(fills), ",".join(symbols), net_realized, gross_realized, fees, exact.get("ok"), _verbose(),
    )
    if _verbose():
        for item in trades:
            log.info("PAPER SELL DB TRADE | %s", _compact(dict(item)))
        for item in orders:
            log.info("PAPER SELL DB ORDER | %s", _compact(dict(item)))
        for item in fills:
            log.info("PAPER SELL DB FILL | %s", _compact(dict(item)))

    return {
        "ok": bool(trades and fills and exact.get("ok")),
        "exact": exact,
        "trades": [dict(item) for item in trades],
        "orders": [dict(item) for item in orders],
        "fills": [dict(item) for item in fills],
        "net_realized_pnl": net_realized,
        "gross_realized_pnl": gross_realized,
        "fees": fees,
    }
