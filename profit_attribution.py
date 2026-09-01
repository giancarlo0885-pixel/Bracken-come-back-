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
    decision_id: str | None
    status: str
    broker_mode: str = "PAPER"
    account_environment: str = "PAPER"
    order_id: str | None = None
    entry_decision_id: str | None = None
    entry_signal_id: str | None = None
    entry_forecast_id: str | None = None
    entry_quote_id: str | None = None
    decision_correlation_id: str | None = None
    model: str | None = None
    model_version: str | None = None
    provider: str | None = None
    provider_symbol: str | None = None
    quote_timestamp: str | None = None
    decision_timestamp: str | None = None
    feature_snapshot: dict[str, Any] | None = None
    risk_snapshot: dict[str, Any] | None = None
    portfolio_snapshot: dict[str, Any] | None = None

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
    decision_id: str | None = None
    broker_mode: str = "PAPER"
    account_environment: str = "PAPER"
    entry_decision_id: str | None = None
    entry_signal_id: str | None = None
    entry_forecast_id: str | None = None
    entry_quote_id: str | None = None
    decision_correlation_id: str | None = None
    model: str | None = None
    model_version: str | None = None
    provider: str | None = None
    provider_symbol: str | None = None
    quote_timestamp: str | None = None
    decision_timestamp: str | None = None
    feature_snapshot: dict[str, Any] | None = None
    risk_snapshot: dict[str, Any] | None = None
    portfolio_snapshot: dict[str, Any] | None = None

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
    decision_id: str | None = None,
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
    decision_id: str | None = None,
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
                entry_decision_id=lot.entry_decision_id or lot.decision_id,
                entry_signal_id=lot.entry_signal_id,
                entry_forecast_id=lot.entry_forecast_id,
                entry_quote_id=lot.entry_quote_id,
                decision_correlation_id=lot.decision_correlation_id,
                model=lot.model,
                model_version=lot.model_version,
                provider=lot.provider,
                provider_symbol=lot.provider_symbol,
                quote_timestamp=lot.quote_timestamp,
                decision_timestamp=lot.decision_timestamp,
                feature_snapshot=lot.feature_snapshot,
                risk_snapshot=lot.risk_snapshot,
                portfolio_snapshot=lot.portfolio_snapshot,
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


def profit_attribution_rows(
    *,
    positions: Iterable[dict[str, Any]],
    ledger_rows: Iterable[TradeLedgerRow | dict[str, Any]],
    quotes: dict[str, dict[str, Any]] | None = None,
    market: str | None = None,
    equity: float = 0.0,
) -> list[dict[str, Any]]:
    """Summarize realized and unrealized P/L by symbol for Portfolio Center."""
    wanted_market = str(market or "").lower().strip()
    quote_map = {normalize_symbol(symbol): dict(quote) for symbol, quote in (quotes or {}).items()}
    by_symbol: dict[str, dict[str, Any]] = {}

    for row in ledger_rows:
        data = row.to_dict() if isinstance(row, TradeLedgerRow) else dict(row)
        row_market = str(data.get("market") or "").lower().strip()
        if wanted_market and row_market != wanted_market:
            continue
        symbol = normalize_symbol(data.get("symbol"))
        if not symbol:
            continue
        bucket = by_symbol.setdefault(
            symbol,
            {
                "symbol": symbol,
                "market": row_market,
                "bucket": data.get("bucket") or "",
                "strategy": data.get("strategy") or "",
                "quantity": 0.0,
                "entry_price": _finite(data.get("entry_price")),
                "exit_or_current_price": _finite(data.get("exit_price")),
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "total_pnl": 0.0,
                "return_pct": 0.0,
                "fees": 0.0,
                "first_entry_time": data.get("entry_time") or data.get("created_at") or "",
                "latest_fill_time": data.get("exit_time") or data.get("created_at") or "",
                "tier": data.get("tier") or "",
                "quote_provider": data.get("quote_provider") or "",
                "status": data.get("status") or "",
                "entry_reason": data.get("entry_reason") or data.get("reason") or "",
                "exit_or_hold_reason": data.get("exit_reason") or data.get("reason") or data.get("status") or "",
            },
        )
        bucket["quantity"] += _finite(data.get("quantity"))
        bucket["realized_pnl"] += _finite(data.get("net_pnl"))
        bucket["fees"] += _finite(data.get("fees"))
        if data.get("entry_time") and (not bucket["first_entry_time"] or str(data["entry_time"]) < str(bucket["first_entry_time"])):
            bucket["first_entry_time"] = data["entry_time"]
        latest = data.get("exit_time") or data.get("created_at")
        if latest and str(latest) > str(bucket.get("latest_fill_time") or ""):
            bucket["latest_fill_time"] = latest
        for key in ("bucket", "strategy", "tier", "quote_provider", "status"):
            if data.get(key):
                bucket[key] = data.get(key)

    for position in positions:
        row_market = str(position.get("market") or "").lower().strip()
        if wanted_market and row_market != wanted_market:
            continue
        symbol = normalize_symbol(position.get("symbol"))
        if not symbol:
            continue
        avg_cost = _finite(position.get("average_price") or position.get("avg_cost") or position.get("entry_price"))
        qty = _finite(position.get("quantity"))
        quote = quote_map.get(symbol)
        if quote is None:
            quote = {
                "symbol": symbol,
                "requested_symbol": symbol,
                "provider_symbol": symbol,
                "price": position.get("current_price") or position.get("price"),
                "quote_verified": position.get("quote_verified") is True,
            }
        mark = unrealized_position_pnl(position, quote)
        current = _finite(mark.get("current_verified_price") or position.get("current_price") or position.get("price"))
        item = by_symbol.setdefault(
            symbol,
            {
                "symbol": symbol,
                "market": row_market,
                "bucket": position.get("bucket") or "",
                "strategy": position.get("strategy") or "",
                "quantity": 0.0,
                "entry_price": avg_cost,
                "exit_or_current_price": current,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "total_pnl": 0.0,
                "return_pct": 0.0,
                "fees": 0.0,
                "first_entry_time": position.get("opened_at") or "",
                "latest_fill_time": position.get("updated_at") or position.get("opened_at") or "",
                "tier": position.get("tier") or "",
                "quote_provider": position.get("quote_provider") or position.get("provider") or "",
                "status": mark["status"],
                "entry_reason": position.get("entry_reason") or position.get("reason") or "",
                "exit_or_hold_reason": position.get("hold_reason") or "Open position",
            },
        )
        item["quantity"] = max(item["quantity"], qty)
        item["entry_price"] = item["entry_price"] or avg_cost
        item["exit_or_current_price"] = current or item["exit_or_current_price"]
        if mark["status"] == "VERIFIED":
            item["unrealized_pnl"] = _finite(item.get("unrealized_pnl")) + _finite(mark.get("unrealized_pnl"))
            item["return_pct"] = _finite(mark.get("unrealized_return_pct"))
            item["status"] = "VERIFIED"
        else:
            # An open position without a verified quote has unknown mark-to-market
            # P/L. Preserve that unknown state instead of converting it to $0.00.
            item["unrealized_pnl"] = None
            item["return_pct"] = None
            item["status"] = mark["status"]
        for key in ("bucket", "strategy", "tier"):
            if position.get(key):
                item[key] = position.get(key)
        if position.get("quote_provider") or position.get("provider"):
            item["quote_provider"] = position.get("quote_provider") or position.get("provider")
        if position.get("opened_at") and (not item["first_entry_time"] or str(position["opened_at"]) < str(item["first_entry_time"])):
            item["first_entry_time"] = position["opened_at"]
        if position.get("updated_at") and str(position["updated_at"]) > str(item.get("latest_fill_time") or ""):
            item["latest_fill_time"] = position["updated_at"]

    output = []
    for item in by_symbol.values():
        item["realized_pnl"] = round(_finite(item.get("realized_pnl")), 10)
        if item.get("unrealized_pnl") is None:
            item["unrealized_pnl"] = None
            item["total_pnl"] = None
            item["return_pct"] = None
            item["contribution_to_portfolio_profit_pct"] = None
        else:
            item["unrealized_pnl"] = round(_finite(item.get("unrealized_pnl")), 10)
            item["total_pnl"] = round(item["realized_pnl"] + item["unrealized_pnl"], 10)
            item["contribution_to_portfolio_profit_pct"] = round((item["total_pnl"] / equity * 100.0), 6) if equity else 0.0
        output.append(item)
    return sorted(output, key=lambda row: _finite(row.get("total_pnl")), reverse=True)
