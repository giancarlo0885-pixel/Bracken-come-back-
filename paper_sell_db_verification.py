from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
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
    }
    return {key: row.get(key) for key in allowed if key in row}


def emit_recent_crypto_sell_db_verification(*, lookback_minutes: int = 180) -> dict[str, Any]:
    """Read and log persisted crypto paper SELL evidence from production Postgres.

    This function performs SELECT queries only. It exists so production lifecycle
    evidence can be verified through the same DATABASE_URL used by the worker when
    an external Railway SQL diagnostic is unavailable.
    """
    from database import rows

    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max(1, lookback_minutes))).isoformat()
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
        return {"ok": False, "reason": f"trades:{exc.__class__.__name__}"}

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
        "PAPER SELL DB VERIFY | status=%s | sells=%d | orders=%d | fills=%d | symbols=%s | net_realized_pnl=%.8f | gross_realized_pnl=%.8f | fees=%.8f",
        "PASS" if trades and fills else "PARTIAL",
        len(trades), len(orders), len(fills), ",".join(symbols), net_realized, gross_realized, fees,
    )
    for item in trades:
        log.info("PAPER SELL DB TRADE | %s", _compact(dict(item)))
    for item in orders:
        log.info("PAPER SELL DB ORDER | %s", _compact(dict(item)))
    for item in fills:
        log.info("PAPER SELL DB FILL | %s", _compact(dict(item)))

    return {
        "ok": bool(trades and fills),
        "trades": [dict(item) for item in trades],
        "orders": [dict(item) for item in orders],
        "fills": [dict(item) for item in fills],
        "net_realized_pnl": net_realized,
        "gross_realized_pnl": gross_realized,
        "fees": fees,
    }
