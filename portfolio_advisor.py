from __future__ import annotations

from dataclasses import dataclass, asdict
from math import sqrt
from typing import Any, Iterable


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class PortfolioHealth:
    equity: float
    cash: float
    invested: float
    margin_debt: float
    buying_power: float
    leverage_used: float
    margin_utilization_pct: float
    cash_pct: float
    position_count: int
    largest_position_pct: float
    concentration_score: float
    liquidity_score: float
    diversification_score: float
    health_score: float
    grade: str
    risk_label: str
    plain_summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def grade(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def analyze_portfolio(
    cash: float,
    positions: Iterable[dict[str, Any]],
    margin_debt: float = 0.0,
    leverage_limit: float = 1.0,
    buying_power: float | None = None,
) -> PortfolioHealth:
    rows = list(positions)
    values = [max(0.0, _f(p.get("quantity")) * _f(p.get("current_price"))) for p in rows]
    invested = sum(values)
    margin_debt = max(0.0, _f(margin_debt))
    equity = max(0.0, cash + invested - margin_debt)
    leverage_limit = max(1.0, _f(leverage_limit, 1.0))
    leverage_used = invested / equity if equity else leverage_limit
    calculated_buying_power = max(0.0, equity * leverage_limit - invested) if equity else 0.0
    buying_power = calculated_buying_power if buying_power is None else max(0.0, _f(buying_power))
    margin_utilization_pct = min(999.0, max(0.0, leverage_used / leverage_limit * 100.0))
    cash_pct = (cash / equity * 100.0) if equity else 0.0
    largest_pct = (max(values) / equity * 100.0) if values and equity else 0.0

    # A leveraged paper broker is judged by excess liquidity and controlled
    # exposure, not by pretending borrowed capital is ordinary cash.
    target_cash_pct = 5.0 if leverage_limit > 1.0 else 15.0
    liquidity = max(0.0, min(100.0, 100.0 - abs(cash_pct - target_cash_pct) * 3.0))
    liquidity -= max(0.0, margin_utilization_pct - 70.0) * 1.7
    liquidity = max(0.0, min(100.0, liquidity))
    concentration = max(0.0, min(100.0, 100.0 - max(0.0, largest_pct - 10.0) * 5.0))
    count = len([v for v in values if v > 0])
    diversification = max(0.0, min(100.0, count * 7.5))
    health = round(0.35 * concentration + 0.30 * liquidity + 0.35 * diversification, 1)

    risk = "Low" if health >= 82 and margin_utilization_pct < 55 else "Moderate" if health >= 65 and margin_utilization_pct < 75 else "High"
    issues: list[str] = []
    if cash_pct < 2 and leverage_limit <= 1.0:
        issues.append("cash is very low")
    elif cash_pct > 40 and invested > 0:
        issues.append("too much capital is sitting in cash")
    if largest_pct > 15:
        issues.append("one holding is too large")
    if count < 8 and invested > 0 and leverage_limit > 1.0:
        issues.append("the institutional paper account needs broader diversification")
    elif count < 4 and invested > 0:
        issues.append("the portfolio has limited diversification")
    if margin_utilization_pct >= 75:
        issues.append("margin utilization is high")
    summary = "Portfolio structure is balanced." if not issues else "Main issue: " + "; ".join(issues) + "."

    return PortfolioHealth(
        equity=round(equity, 2), cash=round(cash, 2), invested=round(invested, 2),
        margin_debt=round(margin_debt, 2), buying_power=round(buying_power, 2),
        leverage_used=round(leverage_used, 2), margin_utilization_pct=round(margin_utilization_pct, 1),
        cash_pct=round(cash_pct, 1), position_count=count,
        largest_position_pct=round(largest_pct, 1), concentration_score=round(concentration, 1),
        liquidity_score=round(liquidity, 1), diversification_score=round(diversification, 1),
        health_score=health, grade=grade(health), risk_label=risk, plain_summary=summary,
    )


def simulate_trade(
    cash: float,
    positions: Iterable[dict[str, Any]],
    action: str,
    symbol: str,
    amount: float,
    assumed_price: float,
    margin_debt: float = 0.0,
    leverage_limit: float = 1.0,
    buying_power: float | None = None,
) -> dict[str, Any]:
    symbol = symbol.strip().upper()
    action = action.strip().lower()
    amount = max(0.0, _f(amount))
    assumed_price = max(0.000001, _f(assumed_price, 1.0))
    proposed = [dict(p) for p in positions]
    before = analyze_portfolio(cash, proposed, margin_debt, leverage_limit, buying_power)
    note = ""

    existing = next((p for p in proposed if str(p.get("symbol", "")).upper() == symbol), None)
    if action == "buy":
        available = before.buying_power if leverage_limit > 1.0 else max(0.0, cash)
        spend = min(amount, max(0.0, available))
        qty = spend / assumed_price
        cash_used = min(max(0.0, cash - before.equity * 0.05), spend)
        margin_debt += max(0.0, spend - cash_used)
        cash -= cash_used
        if existing:
            old_qty = _f(existing.get("quantity"))
            old_avg = _f(existing.get("average_price") or existing.get("entry_price"), assumed_price)
            new_qty = old_qty + qty
            existing["quantity"] = new_qty
            existing["average_price"] = ((old_qty * old_avg) + spend) / new_qty if new_qty else assumed_price
            existing["current_price"] = assumed_price
        else:
            proposed.append({"symbol": symbol, "quantity": qty, "current_price": assumed_price,
                             "entry_price": assumed_price, "average_price": assumed_price})
        note = f"Hypothetically buy ${spend:,.2f} of {symbol}."
    elif action == "sell":
        if not existing:
            note = f"{symbol} is not currently held, so no sale was simulated."
        else:
            current_value = _f(existing.get("quantity")) * assumed_price
            proceeds = min(amount, current_value)
            qty_to_sell = proceeds / assumed_price
            remaining = max(0.0, _f(existing.get("quantity")) - qty_to_sell)
            repayment = min(margin_debt, proceeds)
            margin_debt -= repayment
            cash += proceeds - repayment
            existing["quantity"] = remaining
            existing["current_price"] = assumed_price
            if remaining <= 1e-10:
                proposed.remove(existing)
            note = f"Hypothetically sell ${proceeds:,.2f} of {symbol}."
    else:
        raise ValueError("action must be 'buy' or 'sell'")

    after = analyze_portfolio(cash, proposed, margin_debt, leverage_limit)
    delta = round(after.health_score - before.health_score, 1)
    verdict = "BETTER" if delta >= 3 else "WORSE" if delta <= -3 else "MIXED"
    reasons: list[str] = []
    if after.cash_pct < before.cash_pct - 3:
        reasons.append("less cash remains available")
    if after.cash_pct > before.cash_pct + 3:
        reasons.append("cash flexibility improves")
    if after.largest_position_pct > before.largest_position_pct + 2:
        reasons.append("concentration increases")
    if after.largest_position_pct < before.largest_position_pct - 2:
        reasons.append("single-position concentration falls")
    if after.position_count > before.position_count:
        reasons.append("the number of holdings increases")
    if not reasons:
        reasons.append("the portfolio structure changes only slightly")

    return {
        "verdict": verdict, "score_change": delta, "note": note,
        "before": before.to_dict(), "after": after.to_dict(),
        "reasons": reasons, "positions": proposed,
    }
