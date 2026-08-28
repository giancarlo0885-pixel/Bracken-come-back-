from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from config import (
    MAX_ENTRY_EXTENSION_FROM_VWAP_PCT,
    MAX_SINGLE_BAR_SPIKE_PCT,
    MAX_SINGLE_STOCK_POSITION_PCT,
    MAX_STOCK_SECTOR_EXPOSURE_PCT,
    MAX_STOCK_SPREAD_PCT,
    MIN_AVG_DOLLAR_VOLUME,
    MIN_AVG_VOLUME,
    MIN_STOCK_PRICE,
    STOCK_CORE_WEIGHTS,
    STOCK_ROTATION_MIN_SCORE_IMPROVEMENT,
)


FOREIGN_SUFFIXES = {
    ".AX", ".PA", ".AS", ".L", ".TO", ".HK", ".T", ".DE",
    ".MI", ".SW", ".ST", ".OL", ".BR", ".MC", ".NS", ".BO",
}
US_EXCHANGES = {"NYSE", "NASDAQ", "NYSEARCA", "ARCA", "AMEX", "BATS", "IEX"}
CORE_BUCKET = "core"
TACTICAL_BUCKET = "tactical"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _symbol(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("symbol")
    return _text(value).upper()


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _pct_value(value: Any) -> float:
    number = _num(value)
    return number / 100.0 if abs(number) > 1.0 else number


def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def is_allowed_us_stock(symbol_or_record: Any) -> bool:
    symbol = _symbol(symbol_or_record)
    if not symbol or symbol.endswith("-USD") or any(symbol.endswith(suffix) for suffix in FOREIGN_SUFFIXES):
        return False
    if isinstance(symbol_or_record, dict):
        market = _text(symbol_or_record.get("market") or symbol_or_record.get("asset_class")).lower()
        if market and market not in {"cash", "stock", "equity", "etf"}:
            return False
        region = _text(symbol_or_record.get("region") or symbol_or_record.get("country")).lower()
        if region and region not in {"us", "usa", "united states", "united states of america"}:
            return False
        exchange = _text(symbol_or_record.get("exchange")).upper()
        if exchange and exchange not in US_EXCHANGES:
            return False
    return True


@dataclass(frozen=True)
class MoverMetrics:
    symbol: str
    change_5m_pct: float = 0.0
    change_15m_pct: float = 0.0
    change_1h_pct: float = 0.0
    change_session_pct: float = 0.0
    relative_volume: float = 1.0
    dollar_volume: float = 0.0
    spread_pct: float = 0.0
    distance_from_vwap_pct: float = 0.0
    breakout_score: float = 0.0
    catalyst_score: float = 0.0
    regime_alignment: float = 50.0
    single_bar_spike_pct: float = 0.0


@dataclass(frozen=True)
class StockPositionPlan:
    symbol: str
    entry: float
    stop_loss: float
    take_profit: float
    tier: str
    strategy: str
    mover_score: float


def mover_score(metrics: MoverMetrics) -> float:
    momentum = (
        min(max(metrics.change_15m_pct, -10), 10) * 2.0
        + min(max(metrics.change_1h_pct, -15), 15) * 1.5
        + min(max(metrics.change_session_pct, -20), 20) * 1.0
    )
    volume = min(max(metrics.relative_volume, 0.0), 5.0) * 10.0
    liquidity_bonus = min(max(metrics.dollar_volume / 100_000_000, 0.0), 2.0) * 10.0
    spread_penalty = min(max(metrics.spread_pct, 0.0) / 0.01, 3.0) * 8.0
    raw = (
        50.0
        + momentum
        + volume
        + liquidity_bonus
        + max(0.0, min(metrics.breakout_score, 100.0)) * 0.20
        + max(0.0, min(metrics.catalyst_score, 100.0)) * 0.15
        + max(0.0, min(metrics.regime_alignment, 100.0)) * 0.15
        - spread_penalty
    )
    return round(max(0.0, min(100.0, raw)), 2)


def should_rotate(*, incoming_score: float, held_score: float, minimum_improvement: float = STOCK_ROTATION_MIN_SCORE_IMPROVEMENT) -> bool:
    return _num(incoming_score) >= _num(held_score) + max(0.0, _num(minimum_improvement))


def sector_capacity_ok(*, current_sector_exposure_pct: float, proposed_position_pct: float, max_sector_pct: float = MAX_STOCK_SECTOR_EXPOSURE_PCT) -> bool:
    return _pct_value(current_sector_exposure_pct) + _pct_value(proposed_position_pct) <= _pct_value(max_sector_pct)


def stock_position_plan(price: float, atr_pct: float, tier: str, *, symbol: str = "", strategy: str = "Momentum", score: float = 0.0) -> StockPositionPlan:
    entry = _num(price)
    atr = max(0.0, _pct_value(atr_pct))
    stop_pct = max(0.02, min(0.07, atr * 1.25))
    target_pct = max(stop_pct * 1.5, atr * 2.0)
    return StockPositionPlan(
        symbol=_symbol(symbol),
        entry=entry,
        stop_loss=round(entry * (1 - stop_pct), 4),
        take_profit=round(entry * (1 + target_pct), 4),
        tier=_text(tier or "C").upper(),
        strategy=strategy,
        mover_score=round(_num(score), 2),
    )


def holding_quality_score(*, weighted_signal_score: float, confidence: float, rr: float, relative_strength: float) -> float:
    return round(
        max(0.0, min(100.0, _num(weighted_signal_score))) * 0.40
        + max(0.0, min(100.0, _num(confidence))) * 0.30
        + min(max(_num(rr), 0.0) / 3.0, 1.0) * 100.0 * 0.15
        + max(0.0, min(100.0, _num(relative_strength))) * 0.15,
        2,
    )


def bucket_for_symbol(symbol: str, explicit_bucket: Any = None) -> str:
    bucket = _text(explicit_bucket).lower()
    if bucket in {CORE_BUCKET, TACTICAL_BUCKET}:
        return bucket
    return CORE_BUCKET if _symbol(symbol) in STOCK_CORE_WEIGHTS else TACTICAL_BUCKET


def core_tactical_quantities(positions: list[dict[str, Any]], symbol: str) -> dict[str, float]:
    wanted = _symbol(symbol)
    totals = {CORE_BUCKET: 0.0, TACTICAL_BUCKET: 0.0}
    for position in positions:
        if _symbol(position) != wanted:
            continue
        bucket = bucket_for_symbol(wanted, position.get("bucket"))
        totals[bucket] += max(0.0, _num(position.get("quantity")))
    return totals


def tactical_sell_quantity(positions: list[dict[str, Any]], symbol: str, requested_quantity: float) -> float:
    totals = core_tactical_quantities(positions, symbol)
    return min(max(0.0, _num(requested_quantity)), totals[TACTICAL_BUCKET])


def verified_stock_quote_ok(record: dict[str, Any], *, max_age_seconds: float = 900.0, now: datetime | None = None) -> tuple[bool, str]:
    if not is_allowed_us_stock(record):
        return False, "foreign or unsupported stock"
    price = _num(record.get("price"))
    if price <= 0 or price < MIN_STOCK_PRICE:
        return False, "invalid or below-minimum stock price"
    if record.get("quote_verified") is not True:
        return False, "unverified quote"
    timestamp = _parse_time(record.get("quote_timestamp") or record.get("timestamp"))
    age = _num(record.get("quote_age_seconds"), -1.0)
    if age < 0 and timestamp is not None:
        age = max(0.0, ((now or datetime.now(timezone.utc)) - timestamp).total_seconds())
    if age < 0 or age > max_age_seconds:
        return False, "stale or unknown quote freshness"
    requested = _text(record.get("requested_symbol") or record.get("symbol")).upper()
    provider_symbol = _text(record.get("provider_symbol") or record.get("symbol")).upper()
    if requested != _symbol(record) or provider_symbol != _symbol(record):
        return False, "symbol identity mismatch"
    return True, "verified stock quote"


def validate_mover_for_entry(record: dict[str, Any], *, now: datetime | None = None) -> tuple[str, str]:
    quote_ok, reason = verified_stock_quote_ok(record, now=now)
    if not quote_ok:
        return "REJECT", reason
    if (
        _num(record.get("avg_volume") or record.get("daily_volume")) < MIN_AVG_VOLUME
        or _num(record.get("avg_dollar_volume") or record.get("dollar_volume")) < MIN_AVG_DOLLAR_VOLUME
    ):
        return "REJECT", "insufficient stock liquidity"
    if _num(record.get("spread_pct")) > MAX_STOCK_SPREAD_PCT:
        return "REJECT", "spread too wide"
    if abs(_pct_value(record.get("distance_from_vwap_pct"))) > MAX_ENTRY_EXTENSION_FROM_VWAP_PCT:
        return "WAIT_FOR_PULLBACK", "entry is extended from VWAP"
    if abs(_pct_value(record.get("single_bar_spike_pct"))) > MAX_SINGLE_BAR_SPIKE_PCT:
        return "WAIT_FOR_PULLBACK", "single-bar spike is too large"
    return "BUY", "qualified liquid verified mover"


def broker_capacity_valid(metrics: dict[str, Any]) -> tuple[bool, str]:
    cash = _num(metrics.get("cash"))
    equity = _num(metrics.get("equity") or metrics.get("portfolio_equity"))
    invested = _num(metrics.get("invested") or metrics.get("positions_value") or metrics.get("gross_exposure"))
    buying_power = _num(metrics.get("buying_power"), -1.0)
    leverage = max(1.0, _num(metrics.get("leverage_limit"), 1.0))
    expected_equity = cash + invested
    if equity <= 0 or cash < 0 or invested < 0:
        return False, "BROKER_CAPACITY_INVALID"
    if abs(expected_equity - equity) > max(1.0, equity * 0.02):
        return False, "BROKER_CAPACITY_INVALID"
    if buying_power < 0 or buying_power > equity * leverage * 1.05:
        return False, "BROKER_CAPACITY_INVALID"
    return True, "broker capacity validated"


def holding_view_rows(positions: list[dict[str, Any]], *, equity: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for position in positions:
        if not is_allowed_us_stock(position):
            continue
        symbol = _symbol(position)
        shares = _num(position.get("quantity"))
        avg = _num(position.get("average_price") or position.get("entry_price"))
        current = _num(position.get("current_price") or position.get("price"))
        value = shares * current if shares > 0 and current > 0 else 0.0
        pnl = (current - avg) * shares if avg > 0 and current > 0 else 0.0
        pnl_pct = ((current / avg) - 1.0) * 100.0 if avg > 0 and current > 0 else 0.0
        rows.append(
            {
                "symbol": symbol,
                "name": position.get("company") or position.get("name") or symbol,
                "bucket": bucket_for_symbol(symbol, position.get("bucket")).upper(),
                "shares": shares,
                "avg_cost": avg,
                "current_price": current,
                "market_value": value,
                "unrealized_pnl": pnl,
                "unrealized_pnl_pct": pnl_pct,
                "portfolio_weight_pct": round((value / equity * 100.0), 4) if equity > 0 else 0.0,
                "sector": position.get("sector"),
                "trade_tier": position.get("tier") or position.get("trade_tier"),
                "strategy": position.get("strategy"),
                "quote_provider": position.get("quote_provider") or position.get("provider"),
                "quote_verified": position.get("quote_verified") is True,
                "quote_age_seconds": _num(position.get("quote_age_seconds"), -1.0),
            }
        )
    return rows


def portfolio_summary(metrics: dict[str, Any], positions: list[dict[str, Any]], trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    cash = _num(metrics.get("cash"))
    invested = sum(max(0.0, _num(position.get("market_value"), _num(position.get("quantity")) * _num(position.get("current_price")))) for position in positions)
    equity = cash + invested
    core = 0.0
    tactical = 0.0
    for position in positions:
        value = max(0.0, _num(position.get("market_value"), _num(position.get("quantity")) * _num(position.get("current_price"))))
        if bucket_for_symbol(_symbol(position), position.get("bucket")) == CORE_BUCKET:
            core += value
        else:
            tactical += value
    today_pnl = sum(_num(trade.get("realized_pnl")) for trade in trades or [])
    total_pnl = equity - _num(metrics.get("starting_balance"), equity)
    return {
        "Stock Portfolio Equity": equity,
        "Cash": cash,
        "Invested": invested,
        "Buying Power": min(_num(metrics.get("buying_power"), cash), max(0.0, equity * max(1.0, _num(metrics.get("leverage_limit"), 1.0)))),
        "Core Exposure": core,
        "Tactical Exposure": tactical,
        "Open Positions": len([position for position in positions if is_allowed_us_stock(position)]),
        "Today's P/L": today_pnl,
        "Total P/L": total_pnl,
    }


def rank_best_movers(records: list[dict[str, Any]], *, now: datetime | None = None) -> dict[str, list[dict[str, Any]]]:
    best: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for record in records:
        if not is_allowed_us_stock(record):
            rejected.append({"symbol": _symbol(record), "reason": "outside U.S. stock/ETF scope"})
            continue
        metrics = MoverMetrics(
            symbol=_symbol(record),
            change_5m_pct=_num(record.get("change_5m_pct")),
            change_15m_pct=_num(record.get("change_15m_pct")),
            change_1h_pct=_num(record.get("change_1h_pct")),
            change_session_pct=_num(record.get("session_change_pct") or record.get("change_1d_pct")),
            relative_volume=_num(record.get("relative_volume"), 1.0),
            dollar_volume=_num(record.get("avg_dollar_volume") or record.get("dollar_volume")),
            spread_pct=_num(record.get("spread_pct")),
            distance_from_vwap_pct=_num(record.get("distance_from_vwap_pct")),
            breakout_score=_num(record.get("breakout_score")),
            catalyst_score=_num(record.get("catalyst_score")),
            regime_alignment=_num(record.get("regime_alignment") or record.get("regime_alignment_score"), 50.0),
            single_bar_spike_pct=_num(record.get("single_bar_spike_pct")),
        )
        score = mover_score(metrics)
        action, reason = validate_mover_for_entry({**record, "symbol": metrics.symbol}, now=now)
        row = {**record, "symbol": metrics.symbol, "mover_score": score, "action": action, "reason": reason}
        if action == "BUY":
            best.append(row)
        elif action == "WAIT_FOR_PULLBACK":
            waiting.append(row)
        else:
            rejected.append(row)
    return {
        "best_movers": sorted(best, key=lambda item: _num(item.get("mover_score")), reverse=True),
        "watchlist": sorted(waiting, key=lambda item: _num(item.get("mover_score")), reverse=True),
        "rejected": rejected,
    }
