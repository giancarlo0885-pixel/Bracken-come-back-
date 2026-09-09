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


def _parse_ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _nearest_matching_order(rows_fn: Any, *, symbol: str, created_at: Any, trade_qty: float) -> dict[str, Any] | None:
    """Match a filled SELL in Python so legacy SQL column types cannot break proof."""
    trade_time = _parse_ts(created_at)
    if trade_time is None:
        return None
    lower = (trade_time - timedelta(minutes=5)).isoformat()
    upper = (trade_time + timedelta(minutes=5)).isoformat()
    candidates = rows_fn(
        """
        SELECT id, order_id, market, symbol, side, status,
               requested_quantity, requested_notional, filled_quantity,
               filled_notional, reference_price, average_fill_price,
               fee_amount, quote_provider, reason, created_at
        FROM paper_orders
        WHERE market=%s
          AND side='SELL'
          AND symbol=%s
          AND status='FILLED'
          AND created_at >= %s
          AND created_at <= %s
        ORDER BY created_at ASC
        LIMIT 50
        """,
        ("crypto", symbol, lower, upper),
    ) or []
    tolerance = max(1e-10, abs(trade_qty) * 1e-6)
    matched: list[tuple[float, dict[str, Any]]] = []
    for raw in candidates:
        item = dict(raw)
        qty = _safe_float(item.get("filled_quantity"))
        if qty is None:
            qty = _safe_float(item.get("requested_quantity"))
        stamp = _parse_ts(item.get("created_at"))
        if qty is None or stamp is None or abs(qty - trade_qty) > tolerance:
            continue
        matched.append((abs((stamp - trade_time).total_seconds()), item))
    if not matched:
        return None
    matched.sort(key=lambda pair: pair[0])
    return matched[0][1]


def _rolling_match_summary(
    trades: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    fills: list[dict[str, Any]],
) -> dict[str, Any]:
    """Prove every sampled SELL has one unique order and one compatible fill."""
    fills_by_order: dict[str, list[dict[str, Any]]] = {}
    for raw in fills:
        fill = dict(raw)
        order_id = str(fill.get("order_id") or "").strip()
        if order_id:
            fills_by_order.setdefault(order_id, []).append(fill)

    unused_order_indexes = set(range(len(orders)))
    matched = 0
    failures: list[dict[str, Any]] = []
    for raw_trade in trades:
        trade = dict(raw_trade)
        symbol = str(trade.get("symbol") or "").upper().strip()
        trade_time = _parse_ts(trade.get("created_at"))
        trade_qty = _safe_float(trade.get("quantity"))
        if not symbol or trade_time is None or trade_qty is None or trade_qty <= 0:
            failures.append({"trade_id": trade.get("id"), "symbol": symbol, "reason": "invalid_trade_evidence"})
            continue

        tolerance = max(1e-10, abs(trade_qty) * 1e-6)
        candidates: list[tuple[float, int, dict[str, Any]]] = []
        for index in unused_order_indexes:
            order = dict(orders[index])
            if str(order.get("symbol") or "").upper().strip() != symbol:
                continue
            if str(order.get("status") or "").upper().strip() != "FILLED":
                continue
            order_time = _parse_ts(order.get("created_at"))
            order_qty = _safe_float(order.get("filled_quantity"))
            if order_qty is None:
                order_qty = _safe_float(order.get("requested_quantity"))
            if order_time is None or order_qty is None:
                continue
            delta = abs((order_time - trade_time).total_seconds())
            if delta > 300 or abs(order_qty - trade_qty) > tolerance:
                continue
            candidates.append((delta, index, order))

        if not candidates:
            failures.append({"trade_id": trade.get("id"), "symbol": symbol, "reason": "no_unique_matching_order"})
            continue
        candidates.sort(key=lambda item: item[0])
        _, order_index, order = candidates[0]
        order_id = str(order.get("order_id") or "").strip()
        compatible_fill = None
        for fill in fills_by_order.get(order_id, []):
            fill_qty = _safe_float(fill.get("quantity"))
            if fill_qty is not None and abs(fill_qty - trade_qty) <= tolerance:
                compatible_fill = fill
                break
        if compatible_fill is None:
            failures.append({"trade_id": trade.get("id"), "symbol": symbol, "order_id": order_id, "reason": "matching_fill_missing_or_quantity_mismatch"})
            continue

        unused_order_indexes.remove(order_index)
        matched += 1

    return {
        "ok": bool(trades) and matched == len(trades) and not failures,
        "matched": matched,
        "sampled": len(trades),
        "failures": failures,
    }


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
    trade_qty = float(trade.get("quantity") or 0.0)
    order = _nearest_matching_order(rows_fn, symbol=symbol, created_at=created_at, trade_qty=trade_qty)
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
    """Read and strictly reconcile sampled crypto paper SELL evidence from Postgres."""
    from database import rows

    default_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max(1, lookback_minutes))).isoformat()
    cutoff = _explicit_cutoff(default_cutoff)

    try:
        exact = _emit_exact_first_sell(rows, cutoff)
    except Exception as exc:
        log.warning("PAPER SELL EXACT VERIFY | status=UNAVAILABLE | error=%s", exc.__class__.__name__)
        exact = {"ok": False, "reason": f"exact:{exc.__class__.__name__}"}

    try:
        trades = [dict(item) for item in (rows(
            """
            SELECT id, market, symbol, side, quantity, price, value,
                   realized_pnl, gross_realized_pnl, fees, reason, created_at
            FROM trades
            WHERE market=%s AND side='SELL' AND created_at >= %s
            ORDER BY created_at ASC
            LIMIT 20
            """,
            ("crypto", cutoff),
        ) or [])]
    except Exception as exc:
        log.warning("PAPER SELL DB VERIFY | trades=UNAVAILABLE | error=%s", exc.__class__.__name__)
        return {"ok": False, "reason": f"trades:{exc.__class__.__name__}", "exact": exact}

    symbols = sorted({str(item.get("symbol") or "").upper() for item in trades if item.get("symbol")})
    orders: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    cutoff_dt = _parse_ts(cutoff)
    evidence_cutoff = (cutoff_dt - timedelta(minutes=5)).isoformat() if cutoff_dt else cutoff
    if symbols:
        placeholders = ",".join(["%s"] * len(symbols))
        try:
            orders = [dict(item) for item in (rows(
                f"""
                SELECT id, order_id, market, symbol, side, status,
                       requested_quantity, requested_notional, filled_quantity,
                       filled_notional, reference_price, average_fill_price,
                       fee_amount, quote_provider, reason, created_at
                FROM paper_orders
                WHERE market=%s AND side='SELL' AND symbol IN ({placeholders}) AND created_at >= %s
                ORDER BY created_at ASC
                LIMIT 80
                """,
                tuple(["crypto", *symbols, evidence_cutoff]),
            ) or [])]
        except Exception as exc:
            log.warning("PAPER SELL DB VERIFY | orders=UNAVAILABLE | error=%s", exc.__class__.__name__)
        try:
            fills = [dict(item) for item in (rows(
                f"""
                SELECT id, fill_id, order_id, market, symbol, side, quantity,
                       reference_price, fill_price, notional, fee_amount,
                       slippage_pct, spread_pct, market_impact_pct,
                       quote_provider, created_at
                FROM paper_fills
                WHERE market=%s AND side='SELL' AND symbol IN ({placeholders}) AND created_at >= %s
                ORDER BY created_at ASC
                LIMIT 80
                """,
                tuple(["crypto", *symbols, evidence_cutoff]),
            ) or [])]
        except Exception as exc:
            log.warning("PAPER SELL DB VERIFY | fills=UNAVAILABLE | error=%s", exc.__class__.__name__)

    rolling = _rolling_match_summary(trades, orders, fills)
    net_realized = sum(float(item.get("realized_pnl") or 0.0) for item in trades)
    gross_realized = sum(float(item.get("gross_realized_pnl") or 0.0) for item in trades)
    fees = sum(float(item.get("fees") or 0.0) for item in trades)
    strict_ok = bool(rolling.get("ok")) and bool(exact.get("ok"))
    status = "PASS" if strict_ok else "WAITING" if not trades else "FAIL"
    log.info(
        "PAPER SELL DB VERIFY | status=%s | sells=%d | orders=%d | fills=%d | rolling_matched=%d/%d | rolling_failures=%d | symbols=%s | net_realized_pnl=%.8f | gross_realized_pnl=%.8f | fees=%.8f | exact=%s | verbose=%s",
        status,
        len(trades),
        len(orders),
        len(fills),
        int(rolling.get("matched") or 0),
        int(rolling.get("sampled") or 0),
        len(rolling.get("failures") or []),
        ",".join(symbols),
        net_realized,
        gross_realized,
        fees,
        exact.get("ok"),
        _verbose(),
    )
    if rolling.get("failures"):
        log.warning("PAPER SELL DB VERIFY | rolling_failures=%s", (rolling.get("failures") or [])[:12])
    if _verbose():
        for item in trades:
            log.info("PAPER SELL DB TRADE | %s", _compact(item))
        for item in orders:
            log.info("PAPER SELL DB ORDER | %s", _compact(item))
        for item in fills:
            log.info("PAPER SELL DB FILL | %s", _compact(item))

    return {
        "ok": strict_ok,
        "exact": exact,
        "rolling": rolling,
        "trades": trades,
        "orders": orders,
        "fills": fills,
        "net_realized_pnl": net_realized,
        "gross_realized_pnl": gross_realized,
        "fees": fees,
    }
