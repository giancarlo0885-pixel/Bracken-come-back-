from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
import uuid
from typing import Any, Iterable

from provider_router import normalize_symbol


try:
    from config import MAX_RECONCILIATION_DIFFERENCE_PCT, PAPER_TAX_LOT_METHOD
except Exception:  # pragma: no cover - config imports are available in normal runtime
    PAPER_TAX_LOT_METHOD = "FIFO"
    MAX_RECONCILIATION_DIFFERENCE_PCT = 0.005


@dataclass
class TradeLedgerRow:
    trade_id: str
    symbol: str
    market: str
    bucket: str
    strategy: str
    side: str
    quantity: float
    entry_time: datetime | None
    entry_price: float | None
    exit_time: datetime | None
    exit_price: float | None
    gross_pnl: float
    fees: float
    net_pnl: float
    return_pct: float
    tier: str | None
    confidence_score: float | None
    weighted_signal_score: float | None
    quote_provider: str | None
    decision_id: int | None
    status: str
    broker_mode: str = "PAPER"
    account_environment: str = "PAPER"
    order_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("entry_time", "exit_time"):
            if data[key] is not None:
                data[key] = data[key].isoformat()
        return data


@dataclass
class PositionLot:
    lot_id: str
    symbol: str
    market: str
    bucket: str
    strategy: str
    opened_at: datetime
    quantity_opened: float
    quantity_remaining: float
    entry_price: float
    entry_fees: float = 0.0
    decision_id: int | None = None
    broker_mode: str = "PAPER"
    account_environment: str = "PAPER"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["opened_at"] = self.opened_at.isoformat()
        return data


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _time(value: Any) -> datetime | None:
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


def create_lot(
    *,
    symbol: str,
    market: str,
    quantity: float,
    entry_price: float,
    opened_at: Any,
    bucket: str = "Tactical",
    strategy: str = "",
    entry_fees: float = 0.0,
    decision_id: int | None = None,
    broker_mode: str = "PAPER",
    account_environment: str = "PAPER",
) -> PositionLot:
    qty = _finite(quantity)
    price = _finite(entry_price)
    if qty <= 0 or price <= 0:
        raise ValueError("lot requires positive quantity and entry price")
    opened = _time(opened_at) or datetime.now(timezone.utc)
    return PositionLot(
        lot_id=str(uuid.uuid4()),
        symbol=normalize_symbol(symbol),
        market=str(market),
        bucket=str(bucket or "Tactical"),
        strategy=str(strategy or ""),
        opened_at=opened,
        quantity_opened=qty,
        quantity_remaining=qty,
        entry_price=price,
        entry_fees=max(0.0, _finite(entry_fees)),
        decision_id=decision_id,
        broker_mode=broker_mode.upper(),
        account_environment=account_environment.upper(),
    )


def realized_long_pnl(entry_price: float, exit_price: float, quantity: float, fees: float = 0.0) -> dict[str, float]:
    entry = _finite(entry_price)
    exit_ = _finite(exit_price)
    qty = _finite(quantity)
    fee = max(0.0, _finite(fees))
    if entry <= 0 or exit_ <= 0 or qty <= 0:
        raise ValueError("realized P/L requires positive entry, exit, and quantity")
    gross = (exit_ - entry) * qty
    net = gross - fee
    return {
        "gross_pnl": round(gross, 10),
        "fees": round(fee, 10),
        "net_pnl": round(net, 10),
        "return_pct": round(((exit_ / entry) - 1.0) * 100.0, 10),
    }


def fifo_close_lots(
    lots: Iterable[PositionLot],
    *,
    quantity: float,
    exit_price: float,
    exit_time: Any,
    fees: float = 0.0,
    tier: str | None = None,
    confidence_score: float | None = None,
    weighted_signal_score: float | None = None,
    quote_provider: str | None = None,
    decision_id: int | None = None,
    order_id: str | None = None,
) -> list[TradeLedgerRow]:
    remaining = _finite(quantity)
    if remaining <= 0:
        raise ValueError("close quantity must be positive")
    exit_dt = _time(exit_time) or datetime.now(timezone.utc)
    ordered = sorted([lot for lot in lots if lot.quantity_remaining > 0], key=lambda lot: lot.opened_at)
    rows: list[TradeLedgerRow] = []
    for lot in ordered:
        if remaining <= 0:
            break
        close_qty = min(lot.quantity_remaining, remaining)
        fee = max(0.0, _finite(fees)) * (close_qty / max(_finite(quantity), close_qty))
        pnl = realized_long_pnl(lot.entry_price, exit_price, close_qty, fee)
        lot.quantity_remaining = round(lot.quantity_remaining - close_qty, 10)
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
                gross_pnl=pnl["gross_pnl"],
                fees=pnl["fees"],
                net_pnl=pnl["net_pnl"],
                return_pct=pnl["return_pct"],
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


def unrealized_position_pnl(position: dict[str, Any], quote: dict[str, Any] | None) -> dict[str, Any]:
    symbol = normalize_symbol(position.get("symbol"))
    if not quote or quote.get("quote_verified") is not True:
        return {"symbol": symbol, "status": "WAITING FOR VERIFIED PRICE", "unrealized_pnl": None, "unrealized_return_pct": None}
    if normalize_symbol(quote.get("symbol")) != symbol or normalize_symbol(quote.get("requested_symbol")) != symbol or normalize_symbol(quote.get("provider_symbol")) != symbol:
        return {"symbol": symbol, "status": "WAITING FOR VERIFIED PRICE", "unrealized_pnl": None, "unrealized_return_pct": None}
    price = _finite(quote.get("price"))
    avg_cost = _finite(position.get("average_price") or position.get("avg_cost") or position.get("entry_price"))
    qty = _finite(position.get("quantity"))
    if price <= 0 or avg_cost <= 0 or qty <= 0:
        return {"symbol": symbol, "status": "WAITING FOR VERIFIED PRICE", "unrealized_pnl": None, "unrealized_return_pct": None}
    return {
        "symbol": symbol,
        "status": "VERIFIED",
        "current_verified_price": price,
        "unrealized_pnl": round((price - avg_cost) * qty, 10),
        "unrealized_return_pct": round(((price / avg_cost) - 1.0) * 100.0, 10),
    }


def rank_profit_contributors(rows: Iterable[TradeLedgerRow | dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = [row.to_dict() if isinstance(row, TradeLedgerRow) else dict(row) for row in rows]
    return sorted(normalized, key=lambda row: _finite(row.get("net_pnl")), reverse=True)


def split_pnl_by_environment(rows: Iterable[TradeLedgerRow | dict[str, Any]]) -> dict[str, dict[str, float]]:
    summary: dict[str, dict[str, float]] = {}
    for row in rows:
        data = row.to_dict() if isinstance(row, TradeLedgerRow) else dict(row)
        environment = str(data.get("account_environment") or "PAPER").upper()
        market = str(data.get("market") or "unknown").lower()
        bucket = summary.setdefault(f"{environment}:{market}", {"gross_pnl": 0.0, "fees": 0.0, "net_pnl": 0.0})
        bucket["gross_pnl"] = round(bucket["gross_pnl"] + _finite(data.get("gross_pnl")), 10)
        bucket["fees"] = round(bucket["fees"] + _finite(data.get("fees")), 10)
        bucket["net_pnl"] = round(bucket["net_pnl"] + _finite(data.get("net_pnl")), 10)
    return summary


def daily_profit_story(rows: Iterable[TradeLedgerRow | dict[str, Any]], day: Any | None = None) -> dict[str, Any]:
    target = (_time(day) or datetime.now(timezone.utc)).date()
    by_symbol: dict[str, float] = {}
    included = []
    for row in rows:
        data = row.to_dict() if isinstance(row, TradeLedgerRow) else dict(row)
        exit_time = _time(data.get("exit_time") or data.get("created_at"))
        if exit_time is None or exit_time.date() != target:
            continue
        symbol = normalize_symbol(data.get("symbol"))
        net = _finite(data.get("net_pnl"))
        by_symbol[symbol] = by_symbol.get(symbol, 0.0) + net
        included.append(data)
    contributors = [{"symbol": symbol, "net_pnl": round(net, 2)} for symbol, net in sorted(by_symbol.items(), key=lambda item: item[1], reverse=True)]
    total = round(sum(item["net_pnl"] for item in contributors), 2)
    return {"date": target.isoformat(), "contributors": contributors, "net_pnl": total, "reconciled": round(sum(_finite(row.get("net_pnl")) for row in included), 2) == total}


def reconcile_portfolio(*, cash: float, positions: Iterable[dict[str, Any]], quotes: dict[str, dict[str, Any]], broker_reported_equity: float | None = None, tolerance_pct: float = MAX_RECONCILIATION_DIFFERENCE_PCT) -> dict[str, Any]:
    validated_cash = _finite(cash)
    market_value = 0.0
    waiting = []
    for position in positions:
        symbol = normalize_symbol(position.get("symbol"))
        mark = unrealized_position_pnl(position, quotes.get(symbol))
        if mark["status"] != "VERIFIED":
            waiting.append(symbol)
            continue
        market_value += _finite(position.get("quantity")) * _finite(mark.get("current_verified_price"))
    calculated = validated_cash + market_value
    if broker_reported_equity is None:
        return {"reconciled": not waiting, "calculated_equity": round(calculated, 2), "waiting_for_verified_price": waiting, "status": "WAITING_FOR_VERIFIED_PRICE" if waiting else "RECONCILED"}
    reported = _finite(broker_reported_equity)
    diff = reported - calculated
    pct = abs(diff) / reported if reported else 0.0
    return {
        "reconciled": not waiting and pct <= tolerance_pct,
        "calculated_equity": round(calculated, 2),
        "broker_reported_equity": round(reported, 2),
        "equity_difference": round(diff, 2),
        "difference_pct": pct,
        "waiting_for_verified_price": waiting,
        "status": "PORTFOLIO_RECONCILIATION_ERROR" if waiting or pct > tolerance_pct else "RECONCILED",
    }
