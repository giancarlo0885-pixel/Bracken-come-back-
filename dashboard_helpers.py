from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def normalized_confidence(value: Any) -> float:
    number = as_float(value, 0.0)
    if number <= 1:
        number *= 100
    return max(0.0, min(100.0, number))


def normalized_score(value: Any) -> float:
    return normalized_confidence(value)


def star_rating(score: Any) -> str:
    number = normalized_score(score)
    filled = max(1, min(5, round(number / 20)))
    return "★" * filled + "☆" * (5 - filled)


def action_class(action: Any) -> str:
    action_text = str(action or "HOLD").upper()
    if action_text == "BUY":
        return "buy"
    if action_text == "SELL":
        return "sell"
    return "hold"


def clean_market(value: Any) -> str:
    text = str(value or "").lower()
    return "stock" if text in {"cash", "stock"} else "crypto" if text == "crypto" else text


def short_reason(record: Any, max_length: int = 180) -> str:
    """Return a readable, safely truncated reason from either a record or text.

    Older dashboard code passed a plain reason string plus a length limit, while
    ranking cards pass a database record. Supporting both forms prevents the
    dashboard from crashing during mixed-version deployments.
    """
    fallback = "Ranked from council score, momentum, confidence, volume, and risk."

    if isinstance(record, dict):
        payload = parse_json(record.get("payload"))
        text = ""
        for key in ("reason", "explanation", "summary"):
            candidate = record.get(key) or payload.get(key)
            if candidate:
                text = str(candidate)
                break
        text = text or fallback
    elif record is None:
        text = fallback
    else:
        text = str(record).strip() or fallback

    try:
        limit = max(12, int(max_length))
    except (TypeError, ValueError):
        limit = 180

    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def worker_is_online(status: Any) -> bool:
    """Return True only for states that mean the process is actively healthy.

    ``stopped`` and unknown values used to be counted as online, which could
    hide dead Railway workers from Mission Control.
    """
    return str(status or "").strip().lower() in {"starting", "running", "idle", "healthy", "online"}


def symbol_currency(symbol: Any, market: Any = "cash") -> str:
    """Return the quote currency used for human-readable asset prices.

    Portfolio totals remain USD. Individual global opportunity prices retain
    their market currency so a Japanese or Indian quote is never shown as USD.
    """
    text = str(symbol or "").upper().strip()
    market_text = str(market or "").lower().strip()
    if market_text == "crypto" or text.endswith("-USD"):
        return "USD"
    suffixes = (
        (".T", "JPY"), (".NS", "INR"), (".BO", "INR"),
        (".DE", "EUR"), (".PA", "EUR"), (".AS", "EUR"),
        (".MI", "EUR"), (".MC", "EUR"), (".L", "GBP"),
        (".SW", "CHF"), (".HK", "HKD"), (".KS", "KRW"),
        (".KQ", "KRW"), (".AX", "AUD"), (".TO", "CAD"),
        (".SA", "BRL"), (".JO", "ZAR"), (".TA", "ILS"),
    )
    for suffix, currency in suffixes:
        if text.endswith(suffix):
            return currency
    return "USD"


def format_asset_price(value: Any, symbol: Any, market: Any = "cash") -> str:
    number = as_float(value, 0.0)
    if number <= 0:
        return "Waiting for price"
    currency = symbol_currency(symbol, market)
    decimals = 4 if market == "crypto" and number < 10 else 2
    return f"{number:,.{decimals}f} {currency}"


def money_text(value: Any, whole: bool = False) -> str:
    number = as_float(value, 0.0)
    decimals = 0 if whole else 2
    return f"${number:,.{decimals}f}"


def _position_value(position: dict[str, Any]) -> float:
    quantity = as_float(position.get("quantity"))
    price = as_float(position.get("current_price") or position.get("price"))
    return max(0.0, quantity * price)


def simple_portfolio_scores(
    metrics: dict[str, Any],
    positions: list[dict[str, Any]],
    opportunity_count: int = 0,
    data_quality_score: float = 100.0,
) -> dict[str, Any]:
    """Child-readable portfolio labels that keep safety separate from cash use."""
    equity = max(0.0, as_float(metrics.get("equity") or metrics.get("portfolio_equity")))
    cash = max(0.0, as_float(metrics.get("cash")))
    invested = max(0.0, as_float(metrics.get("invested") or metrics.get("positions_value") or metrics.get("gross_exposure")))
    margin_debt = max(0.0, as_float(metrics.get("margin_debt")))
    leverage_used = max(0.0, as_float(metrics.get("leverage_used")))
    leverage_limit = max(1.0, as_float(metrics.get("leverage_limit"), 1.0))
    margin_utilization = max(0.0, as_float(metrics.get("margin_utilization_pct")))
    margin_call = bool(metrics.get("margin_call"))
    position_count = len(positions)
    invested_pct = (invested / equity * 100.0) if equity else 0.0
    cash_pct = (cash / equity * 100.0) if equity else 0.0

    if margin_call or margin_utilization >= 75 or leverage_used >= leverage_limit * 0.85:
        safety = "HIGH RISK"
        safety_score = 30
    elif margin_debt > 0 or margin_utilization >= 35 or leverage_used >= leverage_limit * 0.45:
        safety = "MEDIUM RISK"
        safety_score = 65
    else:
        safety = "LOW RISK"
        safety_score = 90

    if invested <= 0 or position_count == 0:
        diversification = "POOR"
        diversification_score = 20
    elif position_count < 4:
        diversification = "NEEDS WORK"
        diversification_score = 45
    elif position_count < 8:
        diversification = "OK"
        diversification_score = 70
    else:
        diversification = "GOOD"
        diversification_score = 90

    if invested_pct < 25:
        capital_use = "MOSTLY CASH"
        status = "NEEDS MORE INVESTMENTS"
        capital_score = 45
        explanation = "Most of your money is still sitting in cash. The Oracle is looking for strong opportunities before investing more."
    elif invested_pct > 92:
        capital_use = "HEAVILY INVESTED"
        status = "WATCH CASH LEVELS"
        capital_score = 55
        explanation = "Most of your money is already invested. The Oracle should be careful before adding more."
    else:
        capital_use = "BALANCED"
        status = "BALANCED"
        capital_score = 80
        explanation = "Your money is split between investments and cash, so the Oracle has room to act carefully."

    if opportunity_count >= 3:
        opportunity = "HIGH"
        opportunity_score = 85
    elif opportunity_count >= 1:
        opportunity = "MEDIUM"
        opportunity_score = 65
    else:
        opportunity = "LOW"
        opportunity_score = 35

    data_score = max(0.0, min(100.0, as_float(data_quality_score, 100.0)))
    overall_score = round(
        safety_score * 0.30
        + diversification_score * 0.20
        + capital_score * 0.20
        + opportunity_score * 0.20
        + data_score * 0.10,
        1,
    )

    return {
        "status": status,
        "safety": safety,
        "diversification": diversification,
        "capital_use": capital_use,
        "opportunity": opportunity,
        "safety_score": safety_score,
        "diversification_score": diversification_score,
        "opportunity_score": opportunity_score,
        "data_quality_score": round(data_score, 1),
        "overall_score": overall_score,
        "cash_pct": cash_pct,
        "invested_pct": invested_pct,
        "money_invested": invested,
        "cash_waiting": cash,
        "position_count": position_count,
        "profit_loss": equity - as_float(metrics.get("starting_balance")),
        "explanation": explanation,
        "overall_explanation": (
            f"Safety is {safety.lower()}, diversification is {diversification.lower()}, "
            f"money use is {capital_use.lower()}, and opportunity is {opportunity.lower()}."
        ),
    }


def simple_money_summary(metrics: dict[str, Any]) -> dict[str, str]:
    start = as_float(metrics.get("starting_balance"))
    equity = as_float(metrics.get("equity"))
    cash = as_float(metrics.get("cash"))
    invested = as_float(metrics.get("invested") or metrics.get("positions_value") or metrics.get("gross_exposure"))
    pnl = equity - start
    if pnl > 0.005:
        sentence = f"You are up {money_text(pnl)}."
    elif pnl < -0.005:
        sentence = f"You are down {money_text(abs(pnl))}."
    else:
        sentence = "You are about even."
    return {
        "Started With": money_text(start, whole=True),
        "Worth Now": money_text(equity, whole=True),
        "Cash": money_text(cash, whole=True),
        "Invested": money_text(invested, whole=True),
        "Profit / Loss": f"{'+' if pnl >= 0 else '-'}{money_text(abs(pnl), whole=True)}",
        "sentence": sentence,
    }


def live_data_status(record: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    age = record.get("quote_age_seconds")
    if age is None:
        age = record.get("data_freshness_seconds")
    age_seconds = as_float(age, -1.0)
    status_text = str(record.get("data_status") or "").lower()
    eligible = bool(record.get("trade_eligible"))

    if eligible and (age_seconds < 0 or age_seconds <= 90):
        label = "LIVE DATA"
        detail = f"Price checked {int(age_seconds)} seconds ago" if age_seconds >= 0 else "Price was checked recently"
        blocks_execution = False
    elif eligible and age_seconds <= 900:
        label = "DELAYED DATA"
        minutes = max(1, int(round(age_seconds / 60)))
        detail = f"Price checked {minutes} minutes ago"
        blocks_execution = False
    elif "stale" in status_text or "old" in status_text or not eligible:
        label = "OLD DATA"
        detail = "Oracle will not trade using this price."
        blocks_execution = True
    else:
        label = "DELAYED DATA"
        detail = "Price timing is being checked."
        blocks_execution = False

    return {"label": label, "detail": detail, "blocks_execution": blocks_execution}


def simple_opportunity_summary(record: dict[str, Any]) -> dict[str, Any]:
    action = str(record.get("action") or "WAIT").upper()
    symbol = str(record.get("symbol") or "").upper()
    price = as_float(record.get("price"))
    target = as_float(record.get("target"))
    possible_gain = target - price if price > 0 and target > 0 else 0.0
    risk = str(record.get("risk") or "MEDIUM").upper()
    if risk not in {"LOW", "MEDIUM", "HIGH"}:
        risk = "MEDIUM"
    if action in {"BUY", "STRONG_BUY", "ACCUMULATE", "LONG"}:
        why = "The price and market signals look strong."
    elif action == "SELL":
        why = "The Oracle thinks this investment may need to be reduced."
    elif action == "HOLD":
        why = "The Oracle thinks waiting is better than making a new move."
    else:
        why = "The Oracle is waiting for stronger evidence."
    return {
        "symbol": symbol,
        "action": action,
        "price_now": money_text(price),
        "target": money_text(target),
        "why": why,
        "possible_gain": f"{'+' if possible_gain >= 0 else '-'}{money_text(abs(possible_gain))} per share",
        "risk": risk,
        "data": live_data_status(record),
    }


def simple_oracle_summary(
    workers_running: bool,
    stock_scores: dict[str, Any],
    crypto_scores: dict[str, Any],
    best_opportunity: dict[str, Any] | None,
) -> list[dict[str, str]]:
    cash_heavy = stock_scores.get("capital_use") == "MOSTLY CASH" or crypto_scores.get("capital_use") == "MOSTLY CASH"
    best_symbol = str((best_opportunity or {}).get("symbol") or "").upper()
    best_text = (
        f"{best_symbol} is currently the highest-ranked opportunity."
        if best_symbol
        else "No investment has passed every check yet."
    )
    return [
        {
            "title": "System is working" if workers_running else "System needs attention",
            "body": "Stock and crypto scanners are running." if workers_running else "One or more scanners has not checked in recently.",
            "tone": "good" if workers_running else "bad",
        },
        {
            "title": "Your money is mostly sitting in cash" if cash_heavy else "Your money is being used carefully",
            "body": "The Oracle is waiting for better investments." if cash_heavy else "The Oracle is balancing investments and cash.",
            "tone": "warn" if cash_heavy else "good",
        },
        {"title": "Best opportunity right now", "body": best_text, "tone": "good" if best_symbol else "warn"},
    ]


def simple_portfolio_builder_plan(
    cash: Any,
    equity: Any,
    opportunities: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    max_position_pct: float = 0.15,
    reserve_pct: float = 0.30,
    max_items: int = 4,
) -> list[dict[str, Any]]:
    """Draft a safe, non-executing allocation plan for display only."""
    cash_value = max(0.0, as_float(cash))
    equity_value = max(cash_value, as_float(equity))
    if cash_value <= 0 or equity_value <= 0:
        return [{"label": "Keep as cash", "symbol": "CASH", "amount": 0.0, "reason": "No cash is available."}]

    existing = {str(p.get("symbol") or "").upper(): _position_value(p) for p in positions}
    max_position_value = equity_value * max(0.01, max_position_pct)
    spendable = max(0.0, cash_value - equity_value * max(0.0, reserve_pct))
    approved_actions = {"BUY", "STRONG_BUY", "ACCUMULATE", "LONG"}
    seen: set[str] = set()
    ranked: list[dict[str, Any]] = []
    for item in opportunities:
        symbol = str(item.get("symbol") or "").upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        if str(item.get("action") or "").upper() not in approved_actions:
            continue
        if item.get("trade_eligible") is False:
            continue
        ranked.append(item)

    plan: list[dict[str, Any]] = []
    remaining = spendable
    labels = ["Best opportunity", "Second opportunity", "Third opportunity", "Diversifying opportunity"]
    for index, item in enumerate(ranked[:max_items]):
        symbol = str(item.get("symbol") or "").upper()
        existing_value = existing.get(symbol, 0.0)
        room = max(0.0, max_position_value - existing_value)
        if room <= 0 or remaining <= 0:
            continue
        score = normalized_score(item.get("score") or item.get("opportunity_score"))
        confidence = normalized_confidence(item.get("confidence"))
        weight = max(0.10, min(0.35, (score + confidence) / 600.0))
        amount = min(room, remaining, cash_value * weight)
        if amount <= 0:
            continue
        plan.append({"label": labels[min(index, len(labels) - 1)], "symbol": symbol, "amount": round(amount, 2), "reason": "Fits current confidence, risk, and position-size limits."})
        remaining -= amount

    keep_cash = max(0.0, cash_value - sum(item["amount"] for item in plan))
    plan.append({"label": "Keep as cash", "symbol": "CASH", "amount": round(keep_cash, 2), "reason": "Keeps a safety reserve and avoids overloading one investment."})
    return plan


def simple_mode_visible_text(samples: list[str]) -> str:
    """Normalize simple-mode copy for tests that guard against technical leakage."""
    return "\n".join(str(item) for item in samples)
