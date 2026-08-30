from __future__ import annotations

import math
import re
from typing import Any, Callable


_TRADES_QUERY = re.compile(r"\bfrom\s+trades\b", re.IGNORECASE)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def fee_aware_trade_row(record: dict[str, Any]) -> dict[str, Any]:
    """Return a web-only trade view with fees represented in visible P/L.

    The execution ledger stores BUY fees explicitly while a BUY has no realized
    price P/L.  Legacy dashboard code sums ``trades.realized_pnl`` directly, so
    a BUY fee would otherwise disappear from dashboard/day P&L.  For the web
    view only, BUY P/L is therefore represented as minus its explicit fee.

    SELL rows already have their exit fee deducted by the paper execution
    accounting layer.  Keeping SELL ``realized_pnl`` unchanged means an
    all-trades aggregate becomes: -entry fees + sell gross P/L - exit fees.
    Canonical database values are not mutated.
    """
    item = dict(record or {})
    side = str(item.get("side") or "").upper().strip()
    fee = max(0.0, _number(item.get("fees")))
    raw_realized = _number(item.get("realized_pnl"))
    item["display_fees"] = fee
    item["raw_realized_pnl"] = raw_realized
    if side == "BUY":
        item["realized_pnl"] = raw_realized - fee
    return item


def install_web_trade_accounting(database_module: Any) -> None:
    """Decorate SELECT * trade rows used by Streamlit without touching storage."""
    if getattr(database_module, "_web_trade_accounting_installed", False):
        return

    original_rows: Callable[..., list[dict[str, Any]]] = database_module.rows
    original_row: Callable[..., dict[str, Any] | None] = database_module.row

    def rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        records = original_rows(query, params)
        if _TRADES_QUERY.search(str(query or "")):
            return [fee_aware_trade_row(record) for record in records]
        return records

    def row(query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        record = original_row(query, params)
        if record is not None and _TRADES_QUERY.search(str(query or "")):
            return fee_aware_trade_row(record)
        return record

    database_module.rows = rows
    database_module.row = row
    database_module._web_trade_accounting_installed = True


def install_readable_trade_fee_column(dashboard_helpers_module: Any) -> None:
    """Add an explicit Fees column to the human-readable trade history table."""
    if getattr(dashboard_helpers_module, "_web_trade_fee_column_installed", False):
        return

    original = dashboard_helpers_module.readable_trade_rows

    def readable_trade_rows(trades: list[dict[str, Any]], limit: int = 50) -> list[dict[str, Any]]:
        rows = original(trades, limit=limit)
        for output, trade in zip(rows, trades[: max(0, int(limit))]):
            fee = max(0.0, _number(trade.get("fees")))
            output["Fees"] = dashboard_helpers_module.money_text(fee)
        return rows

    dashboard_helpers_module.readable_trade_rows = readable_trade_rows
    dashboard_helpers_module._web_trade_fee_column_installed = True
