from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from config import DECISION_CRYPTO_MAX_AGE_MINUTES, DECISION_STOCK_MAX_AGE_MINUTES
from capital_allocator import adaptive_capital_allocation, max_positions_for_equity
from market_sessions import quote_is_fresh
from stock_best_movers import (
    holding_quality_score,
    holding_view_rows,
    is_allowed_us_stock as stock_scope_allowed,
    should_rotate,
)


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


def compact_money_text(value: Any, *, signed: bool = False) -> str:
    number = as_float(value, 0.0)
    sign = ""
    if signed:
        sign = "+" if number >= 0 else "-"
    absolute = abs(number)
    for threshold, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if absolute >= threshold:
            scaled = absolute / threshold
            decimals = 0 if scaled >= 100 else 1
            return f"{sign}${scaled:,.{decimals}f}{suffix}"
    decimals = 0 if absolute >= 10 or absolute == round(absolute) else 2
    return f"{sign}${absolute:,.{decimals}f}"


def signed_money_text(value: Any, whole: bool = False) -> str:
    number = as_float(value, 0.0)
    sign = "+" if number >= 0 else "-"
    return f"{sign}{money_text(abs(number), whole=whole)}"


def signed_compact_money_text(value: Any) -> str:
    return compact_money_text(value, signed=True)


def parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        text = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


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
    timestamp = parse_timestamp(record.get("quote_timestamp") or record.get("timestamp"))
    age_seconds = as_float(age, -1.0)
    if age_seconds < 0 and timestamp is not None:
        age_seconds = max(0.0, (now - timestamp).total_seconds())

    status_text = str(record.get("data_status") or "").lower()
    verified = record.get("quote_verified") is True
    has_freshness_evidence = age_seconds >= 0 or timestamp is not None
    symbol = str(record.get("symbol") or "").upper().strip()
    market = str(record.get("market") or "").lower().strip()
    interval = str(record.get("source_interval") or record.get("interval") or "").lower().strip()
    provider_mode = str(
        record.get("provider_mode") or record.get("source_mode") or record.get("mode") or ""
    ).lower()
    is_eod = "eod" in provider_mode or interval in {"1d", "daily", "day"}
    is_crypto = market == "crypto" or symbol.endswith("-USD")
    max_age_seconds = (
        DECISION_CRYPTO_MAX_AGE_MINUTES if is_crypto else DECISION_STOCK_MAX_AGE_MINUTES
    ) * 60

    execution_fresh = False
    if verified and timestamp is not None and interval:
        execution_fresh = quote_is_fresh(
            timestamp,
            interval or "1d",
            now=now,
            max_intraday_age_seconds=max_age_seconds,
            exchange=record.get("exchange") or "",
            region=record.get("region") or record.get("country") or "",
            symbol=symbol,
        )
    elif verified and age_seconds >= 0 and not is_eod:
        execution_fresh = age_seconds <= max_age_seconds

    if "stale" in status_text or "old" in status_text:
        label = "OLD DATA"
        detail = "Oracle will not trade using this price."
        blocks_execution = True
    elif not has_freshness_evidence:
        label = "FRESHNESS UNKNOWN"
        detail = "Oracle cannot verify when this price was checked."
        blocks_execution = True
    elif not verified:
        label = "OLD DATA"
        detail = "Oracle has not verified this price for execution."
        blocks_execution = True
    elif not execution_fresh:
        label = "OLD DATA"
        detail = "Oracle will not trade using this price."
        blocks_execution = True
    elif not is_eod and 0 <= age_seconds <= 90:
        label = "LIVE DATA"
        detail = f"Price checked {int(age_seconds)} seconds ago"
        blocks_execution = False
    else:
        label = "DELAYED DATA"
        if age_seconds >= 0:
            minutes = max(1, int(round(age_seconds / 60)))
            detail = f"Verified execution-eligible price checked {minutes} minutes ago"
        else:
            detail = "Verified completed-market price is eligible under the execution freshness rules."
        blocks_execution = False

    return {"label": label, "detail": detail, "blocks_execution": blocks_execution}


def data_age_label(record: dict[str, Any], now: datetime | None = None) -> str:
    status = live_data_status(record, now=now)
    if status["label"] == "FRESHNESS UNKNOWN":
        return "Unknown"
    if status["label"] == "OLD DATA":
        return "Old"
    age = record.get("quote_age_seconds")
    if age is None:
        age = record.get("data_freshness_seconds")
    timestamp = parse_timestamp(record.get("quote_timestamp") or record.get("timestamp"))
    seconds = as_float(age, -1.0)
    if seconds < 0 and timestamp is not None:
        reference = now or datetime.now(timezone.utc)
        seconds = max(0.0, (reference - timestamp).total_seconds())
    if seconds < 0:
        return "Unknown"
    if seconds < 60:
        return f"{int(seconds)} sec"
    if seconds < 3600:
        return f"{max(1, int(round(seconds / 60)))} min"
    return f"{max(1, int(round(seconds / 3600)))} hr"


def _clean_action(value: Any) -> str:
    action = str(value or "WAIT").upper()
    if action in {"STRONG_BUY", "ACCUMULATE", "LONG"}:
        return "BUY"
    if action in {"BUY", "SELL", "HOLD", "WAIT"}:
        return action
    return "WAIT"


def balanced_money_bar(metrics: dict[str, Any], trades: list[dict[str, Any]], now: datetime | None = None) -> list[dict[str, str]]:
    reference = now or datetime.now(timezone.utc)
    today = reference.date()
    equity = as_float(metrics.get("equity"))
    starting = as_float(metrics.get("starting_balance"))
    cash = as_float(metrics.get("cash"))
    invested = as_float(metrics.get("invested") or metrics.get("positions_value") or metrics.get("gross_exposure"))
    today_pnl = 0.0
    for trade in trades:
        created = parse_timestamp(trade.get("created_at"))
        if created is not None and created.date() == today:
            today_pnl += as_float(trade.get("realized_pnl"))
    return [
        {"label": "PORTFOLIO VALUE", "value": compact_money_text(equity)},
        {"label": "CASH", "value": compact_money_text(cash)},
        {"label": "INVESTED", "value": compact_money_text(invested)},
        {"label": "TOTAL PROFIT / LOSS", "value": signed_compact_money_text(equity - starting)},
        {"label": "TODAY", "value": signed_compact_money_text(today_pnl)},
    ]


def balanced_opportunity_rows(decisions: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in decisions[: max(0, int(limit))]:
        price = as_float(item.get("price"))
        target = as_float(item.get("target"))
        expected = as_float(item.get("expected_return"))
        if expected == 0 and price > 0 and target > 0:
            expected = ((target / price) - 1.0) * 100.0
        action = _clean_action(item.get("action"))
        data_status = live_data_status(item)
        execution_blocked = bool(item.get("trade_eligible") is False or data_status["blocks_execution"])
        if action == "BUY" and execution_blocked:
            action_label = "YELLOW WATCH"
        else:
            action_label = {"BUY": "GREEN BUY", "SELL": "RED SELL", "HOLD": "YELLOW HOLD", "WAIT": "YELLOW WAIT"}.get(action, "YELLOW WAIT")
        rows.append(
            {
                "Action": action_label,
                "Symbol": str(item.get("symbol") or "").upper(),
                "Price": money_text(price),
                "Target": money_text(target),
                "Possible Gain %": f"{expected:+.1f}%",
                "Confidence": f"{normalized_confidence(item.get('confidence')):.0f}%",
                "Risk": str(item.get("risk") or "Medium").title(),
                "Data Age": data_age_label(item),
            }
        )
    return rows


def balanced_portfolio_rows(positions: list[dict[str, Any]], equity: Any) -> list[dict[str, Any]]:
    equity_value = max(0.0, as_float(equity))
    rows: list[dict[str, Any]] = []
    for position in positions:
        symbol = str(position.get("symbol") or "").upper()
        quantity = as_float(position.get("quantity"))
        average = as_float(position.get("average_price") or position.get("entry_price"))
        current = as_float(position.get("current_price") or position.get("price"))
        value = max(0.0, quantity * current)
        allocation = (value / equity_value * 100.0) if equity_value else 0.0
        pnl = (current - average) * quantity if current > 0 and average > 0 else 0.0
        pnl_pct = ((current / average) - 1.0) * 100.0 if average > 0 and current > 0 else 0.0
        if allocation >= 20 or pnl_pct <= -12:
            status = "HIGH RISK"
        elif allocation >= 10 or pnl_pct <= -5:
            status = "WATCH"
        else:
            status = "GOOD"
        rows.append(
            {
                "Symbol": symbol,
                "Value": money_text(value),
                "Allocation %": f"{allocation:.1f}%",
                "Avg Price": money_text(average),
                "Current Price": money_text(current),
                "Profit/Loss": signed_money_text(pnl),
                "Status": status,
            }
        )
    return rows


def balanced_data_status(stocks_online: bool, crypto_online: bool, provider_rows: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
    provider_rows = provider_rows or []
    limited = any(str(row.get("status") or row.get("severity") or "").lower() in {"limited", "degraded", "cooldown", "warning"} for row in provider_rows)
    return [
        {"Area": "STOCKS", "Status": "GREEN" if stocks_online else "RED"},
        {"Area": "CRYPTO", "Status": "GREEN" if crypto_online else "RED"},
        {"Area": "NEWS", "Status": "YELLOW" if limited else "GREEN"},
        {"Area": "GLOBAL", "Status": "YELLOW" if limited else "GREEN"},
    ]


def simple_opportunity_summary(record: dict[str, Any]) -> dict[str, Any]:
    action = str(record.get("action") or "WAIT").upper()
    symbol = str(record.get("symbol") or "").upper()
    price = as_float(record.get("price"))
    target = as_float(record.get("target"))
    possible_gain = target - price if price > 0 and target > 0 else 0.0
    risk = str(record.get("risk") or "MEDIUM").upper()
    if risk not in {"LOW", "MEDIUM", "HIGH"}:
        risk = "MEDIUM"
    data = live_data_status(record)
    entry_action = action in {"BUY", "STRONG_BUY", "ACCUMULATE", "LONG"}
    display_action = action
    if entry_action and data["blocks_execution"]:
        display_action = "WATCH"
        why = "The setup is interesting, but Oracle is waiting for verified fresh price data before treating it as tradable."
    elif entry_action:
        why = "The price and market signals look strong."
    elif action == "SELL":
        why = "The Oracle thinks this investment may need to be reduced."
    elif action == "HOLD":
        why = "The Oracle thinks waiting is better than making a new move."
    else:
        why = "The Oracle is waiting for stronger evidence."
    return {
        "symbol": symbol,
        "action": display_action,
        "price_now": money_text(price),
        "target": money_text(target),
        "why": why,
        "possible_gain": f"{'+' if possible_gain >= 0 else '-'}{money_text(abs(possible_gain))} per share",
        "risk": risk,
        "data": data,
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


def capital_deployment_status(
    metrics: dict[str, Any],
    opportunities: list[dict[str, Any]],
    *,
    market: str = "cash",
    session_label: str = "",
    reserve_pct: float = 0.05,
) -> dict[str, str]:
    equity = max(0.0, as_float(metrics.get("equity")))
    cash = max(0.0, as_float(metrics.get("cash")))
    deployable_cash = max(0.0, cash - equity * reserve_pct) if equity else 0.0
    approved_actions = {"BUY", "STRONG_BUY", "ACCUMULATE", "LONG"}
    related = [item for item in opportunities if str(item.get("market") or market).lower() == market]
    qualified: list[dict[str, Any]] = []
    quote_blocked = False
    metadata_blocked = False
    for item in related:
        if str(item.get("action") or "").upper() not in approved_actions:
            continue
        data = live_data_status(item)
        if data["blocks_execution"] or data["label"] in {"FRESHNESS UNKNOWN", "OLD DATA"}:
            quote_blocked = True
            continue
        required = ("scan_type", "source_interval", "quote_timestamp", "requested_symbol", "provider_symbol")
        if any(item.get(field) in (None, "") for field in required):
            metadata_blocked = True
            continue
        if item.get("trade_eligible") is False:
            continue
        qualified.append(item)
    session = str(session_label or "").lower()
    if market == "cash" and "closed" in session and not qualified:
        return {
            "status": "paused_market_closed",
            "message": "Stock capital deployment paused - market closed and waiting for verified execution quotes.",
        }
    if quote_blocked and not qualified:
        return {
            "status": "blocked_quote",
            "message": "Capital deployment blocked - candidate quote failed execution verification.",
        }
    if metadata_blocked and not qualified:
        return {
            "status": "blocked_metadata",
            "message": "Capital deployment blocked - signal metadata incomplete.",
        }
    if not qualified:
        return {
            "status": "waiting_for_opportunity",
            "message": "Capital available - waiting for qualified opportunities.",
        }
    if deployable_cash > 0:
        return {
            "status": "deployable_cash_available",
            "message": "Too much capital is sitting in cash - qualified opportunities are available and will still pass hard execution checks before any paper order.",
        }
    return {
        "status": "reserve_protected",
        "message": "Final 5% reserve protected - no extra capital is available without breaking the cash reserve.",
    }


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
    risk_weights = {"LOW": 1.0, "MEDIUM": 0.72, "HIGH": 0.38}
    for item in opportunities:
        symbol = str(item.get("symbol") or "").upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        if str(item.get("action") or "").upper() not in approved_actions:
            continue
        if item.get("trade_eligible") is False:
            continue
        data = live_data_status(item)
        if data["blocks_execution"] or data["label"] == "FRESHNESS UNKNOWN":
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
        data = live_data_status(item)
        score = normalized_score(item.get("score") or item.get("opportunity_score"))
        confidence = normalized_confidence(item.get("confidence"))
        expected_return = max(0.0, as_float(item.get("expected_return") or item.get("expected_move_pct")))
        risk = str(item.get("risk") or "MEDIUM").upper()
        risk_weight = risk_weights.get(risk, 0.72)
        freshness_weight = 1.0 if data["label"] == "LIVE DATA" else 0.55
        raw_weight = ((score / 100.0) * 0.35 + (confidence / 100.0) * 0.30 + min(expected_return / 12.0, 1.0) * 0.20 + risk_weight * 0.15)
        weight = max(0.05, min(0.35, raw_weight * freshness_weight))
        amount = min(room, remaining, cash_value * weight)
        if amount <= 0:
            continue
        plan.append({"label": labels[min(index, len(labels) - 1)], "symbol": symbol, "amount": round(amount, 2), "reason": "Fits current confidence, risk, and position-size limits."})
        remaining -= amount

    keep_cash = max(0.0, cash_value - sum(item["amount"] for item in plan))
    plan.append({"label": "Keep as cash", "symbol": "CASH", "amount": round(keep_cash, 2), "reason": "Keeps a safety reserve and avoids overloading one investment."})
    return plan


def capital_allocation_rows(
    opportunities: list[dict[str, Any]],
    metrics: dict[str, Any],
    positions: list[dict[str, Any]],
    *,
    market: str,
    limit: int = 8,
) -> list[dict[str, Any]]:
    equity = as_float(metrics.get("equity"))
    cash = as_float(metrics.get("cash"))
    current_exposure = as_float(metrics.get("invested") or metrics.get("gross_exposure"))
    rows: list[dict[str, Any]] = []
    for item in opportunities[: max(0, int(limit))]:
        symbol = str(item.get("symbol") or "").upper()
        existing = sum(
            _position_value(position)
            for position in positions
            if str(position.get("symbol") or "").upper() == symbol
        )
        price = as_float(item.get("price"))
        stop = as_float(item.get("stop") or item.get("stop_loss") or price * 0.94)
        decision = adaptive_capital_allocation(
            symbol=symbol,
            market=market,
            equity=equity,
            cash=cash,
            current_exposure=current_exposure,
            price=price,
            stop_price=stop,
            tier=str(item.get("tier") or item.get("risk_tier") or "B"),
            confidence=item.get("confidence"),
            reward_risk=as_float(item.get("reward_risk_ratio"), 1.5),
            market_regime=str(item.get("market_regime") or item.get("crypto_regime") or "neutral"),
            dollar_volume=as_float(item.get("avg_dollar_volume") or item.get("dollar_volume_24h") or item.get("liquidity")),
            spread_pct=as_float(item.get("spread_pct")),
            drawdown_pct=as_float(metrics.get("drawdown_pct") or metrics.get("drawdown")),
            existing_position_value=existing,
            buying_power=metrics.get("buying_power"),
            buying_power_validated=metrics.get("buying_power_validated") is True,
        )
        rows.append(
            {
                "Symbol": symbol,
                "Tier": str(item.get("tier") or item.get("risk_tier") or "B").upper(),
                "Confidence": f"{normalized_confidence(item.get('confidence')):.0f}%",
                "Base Risk $": money_text(equity * 0.01),
                "Adjusted Risk $": money_text(decision.risk_budget_dollars),
                "Position Size $": money_text(decision.calculated_notional),
                "Quantity": format_quantity(decision.calculated_quantity),
                "Portfolio Weight": f"{(decision.calculated_notional / equity * 100 if equity else 0):.1f}%",
                "Why This Size": decision.reason,
                "Approved": decision.approved,
            }
        )
    if not rows:
        rows.append(
            {
                "Symbol": "CASH",
                "Tier": "",
                "Confidence": "",
                "Base Risk $": money_text(equity * 0.01),
                "Adjusted Risk $": "$0.00",
                "Position Size $": "$0.00",
                "Quantity": "0",
                "Portfolio Weight": "0.0%",
                "Why This Size": f"Waiting for qualified opportunities. Max open positions for this equity: {max_positions_for_equity(equity)}.",
                "Approved": False,
            }
        )
    return rows


def simple_mode_visible_text(samples: list[str]) -> str:
    """Normalize simple-mode copy for tests that guard against technical leakage."""
    return "\n".join(str(item) for item in samples)


def format_quantity(value: Any) -> str:
    quantity = as_float(value)
    if quantity == 0:
        return "0"
    if abs(quantity) >= 1_000_000:
        return f"{quantity:,.0f}"
    if abs(quantity) >= 1_000:
        return f"{quantity:,.2f}".rstrip("0").rstrip(".")
    if abs(quantity) >= 1:
        return f"{quantity:,.4f}".rstrip("0").rstrip(".")
    return f"{quantity:.8f}".rstrip("0").rstrip(".")


def trade_value_matches_quantity_price(trade: dict[str, Any], tolerance: float = 0.01) -> bool:
    quantity = as_float(trade.get("quantity"))
    price = as_float(trade.get("price"))
    value = as_float(trade.get("value"))
    return abs(quantity * price - value) <= max(tolerance, abs(value) * 0.000001)


def readable_trade_rows(trades: list[dict[str, Any]], limit: int = 50) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for trade in trades[: max(0, int(limit))]:
        side = str(trade.get("side") or "").upper()
        output.append(
            {
                "Date": str(trade.get("created_at") or ""),
                "Bought / Sold": "Bought" if side == "BUY" else "Sold" if side == "SELL" else side.title(),
                "Asset": str(trade.get("symbol") or "").upper(),
                "Price": money_text(trade.get("price")),
                "Money Used": money_text(trade.get("value")),
                "Profit / Loss": f"{'+' if as_float(trade.get('realized_pnl')) >= 0 else '-'}{money_text(abs(as_float(trade.get('realized_pnl'))))}",
                "Quantity": format_quantity(trade.get("quantity")),
                "Reason": str(trade.get("reason") or ""),
                "Trade Value Arithmetic": "OK" if trade_value_matches_quantity_price(trade) else "Check",
            }
        )
    return output


def is_us_crypto_scope(record: dict[str, Any]) -> bool:
    symbol = str(record.get("symbol") or "").upper().strip()
    asset_class = str(record.get("asset_class") or record.get("market") or "").lower().strip()
    exchange = str(record.get("exchange") or "").upper().strip()
    region = str(record.get("region") or record.get("country") or "").lower().strip()
    if asset_class == "crypto" or symbol.endswith("-USD"):
        return True
    if asset_class not in {"stock", "equity", "etf", "cash", ""}:
        return False
    if any(symbol.endswith(suffix) for suffix in (".L", ".TO", ".T", ".HK", ".DE", ".PA", ".AS", ".MI", ".MC", ".NS", ".AX")):
        return False
    if region and region not in {"us", "usa", "united states", "united states of america"}:
        return False
    if exchange and exchange in {"LSE", "XLON", "TSX", "XTSE", "XETRA", "XETR", "TSE", "JPX", "HKEX", "ASX", "NSE"}:
        return False
    return True


def entry_quality_action(record: dict[str, Any]) -> str:
    action = str(record.get("action") or "WAIT").upper()
    entry_actions = {"BUY", "STRONG_BUY", "ACCUMULATE", "LONG"}
    if action not in entry_actions:
        return action if action in {"SELL", "HOLD", "WAIT", "REDUCE"} else "WAIT"
    data = live_data_status(record)
    if data["blocks_execution"]:
        return "WAIT FOR VERIFIED PRICE"
    extended_pct = as_float(record.get("extension_pct") or record.get("distance_from_support_pct"))
    if extended_pct >= 12:
        return "WAIT FOR PULLBACK" if clean_market(record.get("market") or record.get("asset_class")) == "stock" else "WAIT"
    return "BUY" if action in {"BUY", "STRONG_BUY", "LONG"} else "ACCUMULATE"


def market_focus_sections(candidates: list[dict[str, Any]], positions: list[dict[str, Any]] | None = None) -> dict[str, list[dict[str, Any]]]:
    positions = positions or []
    scoped = [dict(item) for item in candidates if is_us_crypto_scope(item)]
    stocks: list[dict[str, Any]] = []
    crypto: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []
    for item in scoped:
        action = entry_quality_action(item)
        view = {**item, "display_action": action}
        engine = "crypto" if clean_market(item.get("market") or item.get("asset_class")) == "crypto" or str(item.get("symbol") or "").upper().endswith("-USD") else "stock"
        if action in {"BUY", "ACCUMULATE"} and not live_data_status(item)["blocks_execution"]:
            (crypto if engine == "crypto" else stocks).append(view)
        else:
            waiting.append(view)
    key = lambda row: as_float(row.get("opportunity_score") or row.get("score"))
    ownership = [
        {
            "symbol": str(position.get("symbol") or "").upper(),
            "market": clean_market(position.get("market") or ("crypto" if str(position.get("symbol") or "").upper().endswith("-USD") else "cash")),
            "quantity": format_quantity(position.get("quantity")),
            "value": money_text(_position_value(position)),
        }
        for position in positions
        if is_us_crypto_scope(position)
    ]
    return {
        "wall_street": sorted(stocks, key=key, reverse=True),
        "crypto": sorted(crypto, key=key, reverse=True),
        "waiting": sorted(waiting, key=key, reverse=True),
        "ownership": ownership,
    }


def is_allowed_us_stock(record: dict[str, Any]) -> bool:
    return stock_scope_allowed(record)


def wall_street_quality_score(record: dict[str, Any]) -> float:
    data = live_data_status(record)
    if data["blocks_execution"]:
        return 0.0
    liquidity = min(as_float(record.get("avg_dollar_volume") or record.get("liquidity")) / 50_000_000, 1.0) * 100
    trend = normalized_score(record.get("momentum_score") or record.get("trend_score") or record.get("score"))
    relative_volume = min(as_float(record.get("relative_volume"), 1.0) / 3.0, 1.0) * 100
    catalyst = normalized_score(record.get("catalyst_score") or (75 if record.get("catalyst") else 40))
    regime = normalized_score(record.get("regime_alignment_score") or record.get("market_regime_score") or 60)
    reward_risk = min(as_float(record.get("reward_risk_ratio"), 1.0) / 3.0, 1.0) * 100
    confidence = normalized_confidence(record.get("confidence"))
    entry_quality = 100.0 if entry_quality_label(record) == "GOOD ENTRY" else 45.0
    return round(
        liquidity * 0.18
        + trend * 0.17
        + relative_volume * 0.13
        + catalyst * 0.11
        + regime * 0.11
        + reward_risk * 0.12
        + confidence * 0.12
        + entry_quality * 0.06,
        2,
    )


def entry_quality_label(record: dict[str, Any]) -> str:
    extension = as_float(record.get("extension_pct") or record.get("distance_from_vwap_pct") or record.get("distance_from_support_pct"))
    if extension >= 18:
        return "TOO EXTENDED"
    if extension >= 10:
        return "WAIT FOR PULLBACK"
    return "GOOD ENTRY"


def wall_street_market_focus(
    candidates: list[dict[str, Any]],
    positions: list[dict[str, Any]] | None = None,
    ledger_rows: list[dict[str, Any]] | None = None,
    *,
    max_trades: int = 15,
) -> dict[str, list[dict[str, Any]]]:
    positions = positions or []
    ledger_rows = ledger_rows or []
    best: list[dict[str, Any]] = []
    movers: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for raw in candidates:
        item = dict(raw)
        if not is_allowed_us_stock(item):
            continue
        data = live_data_status(item)
        action = entry_quality_action(item)
        quality = wall_street_quality_score(item)
        if data["blocks_execution"]:
            rejected.append({"Symbol": str(item.get("symbol") or "").upper(), "Reason": data["label"], "Details": data["detail"]})
            continue
        if action == "WAIT FOR PULLBACK":
            rejected.append({"Symbol": str(item.get("symbol") or "").upper(), "Reason": "OVEREXTENDED", "Details": "Entry is stretched beyond the pullback threshold."})
        if action in {"BUY", "ACCUMULATE"}:
            best.append(
                {
                    "Rank": 0,
                    "Symbol": str(item.get("symbol") or "").upper(),
                    "Company": item.get("company") or item.get("name") or "",
                    "Price": format_asset_price(item.get("price"), item.get("symbol"), "cash"),
                    "Session Change %": f"{as_float(item.get('session_change_pct') or item.get('change_pct') or item.get('change_1d_pct')):+.1f}%",
                    "Relative Volume": f"{as_float(item.get('relative_volume'), 1.0):.2f}x",
                    "Mover Score": quality,
                    "Trend/Momentum": round(normalized_score(item.get("momentum_score") or item.get("trend_score") or item.get("score")), 1),
                    "Volume/Liquidity": compact_money_text(item.get("avg_dollar_volume") or item.get("liquidity")),
                    "Catalyst": item.get("catalyst") or item.get("catalyst_summary") or "No catalyst supplied",
                    "Market Regime": str(item.get("market_regime") or "NEUTRAL").upper(),
                    "Risk/Reward": f"{as_float(item.get('reward_risk_ratio'), 0.0):.2f}",
                    "Signals Supporting": int(as_float(item.get("signals_supporting") or item.get("signals_agree") or 0)),
                    "Confidence": f"{normalized_confidence(item.get('confidence')):.0f}%",
                    "Tier": str(item.get("tier") or item.get("risk_tier") or ""),
                    "Entry": format_asset_price(item.get("entry") or item.get("price"), item.get("symbol"), "cash"),
                    "Stop": format_asset_price(item.get("stop") or item.get("stop_loss"), item.get("symbol"), "cash"),
                    "Target": format_asset_price(item.get("target") or item.get("target_price"), item.get("symbol"), "cash"),
                    "Action": "ADD" if item.get("existing_position") else ("ROTATE INTO" if item.get("rotation_candidate") else "BUY"),
                    "Data Status": data["label"],
                    "_score": quality,
                }
            )
        movers.append(
            {
                "Symbol": str(item.get("symbol") or "").upper(),
                "5m %": f"{as_float(item.get('change_5m_pct')):+.1f}%",
                "15m %": f"{as_float(item.get('change_15m_pct')):+.1f}%",
                "1h %": f"{as_float(item.get('change_1h_pct')):+.1f}%",
                "Session %": f"{as_float(item.get('session_change_pct') or item.get('change_pct') or item.get('change_1d_pct')):+.1f}%",
                "Relative Volume": f"{as_float(item.get('relative_volume'), 1.0):.2f}x",
                "Dollar Volume": compact_money_text(item.get("avg_dollar_volume") or item.get("liquidity")),
                "Distance From VWAP": f"{as_float(item.get('distance_from_vwap_pct')):+.1f}%",
                "Mover Score": quality,
                "Entry Quality": entry_quality_label(item),
            }
        )

    best = sorted(best, key=lambda row: as_float(row.get("_score")), reverse=True)[:max_trades]
    for index, row in enumerate(best, start=1):
        row["Rank"] = index
        row.pop("_score", None)

    equity = sum(_position_value(position) for position in positions)
    owned = []
    for view in holding_view_rows(positions, equity=equity):
        data_label = "VERIFIED" if view["quote_verified"] else "NEEDS PRICE"
        h_score = holding_quality_score(
            weighted_signal_score=next((position.get("weighted_signal_score") or position.get("score") or 50 for position in positions if str(position.get("symbol") or "").upper() == view["symbol"]), 50),
            confidence=next((position.get("confidence") or 50 for position in positions if str(position.get("symbol") or "").upper() == view["symbol"]), 50),
            rr=next((position.get("reward_risk_ratio") or 1.5 for position in positions if str(position.get("symbol") or "").upper() == view["symbol"]), 1.5),
            relative_strength=next((position.get("relative_strength") or position.get("holding_score") or position.get("score") or 50 for position in positions if str(position.get("symbol") or "").upper() == view["symbol"]), 50),
        )
        owned.append(
            {
                "Symbol": view["symbol"],
                "Company": view["name"],
                "Bucket": view["bucket"],
                "Shares": format_quantity(view["shares"]),
                "Avg Cost": money_text(view["avg_cost"]),
                "Current Price": money_text(view["current_price"]),
                "Market Value": money_text(view["market_value"]),
                "P/L $": signed_money_text(view["unrealized_pnl"]),
                "P/L %": f"{view['unrealized_pnl_pct']:+.1f}%",
                "Weight": f"{view['portfolio_weight_pct']:.1f}%",
                "Sector": view["sector"] or "Unknown",
                "Tier": view["trade_tier"] or "",
                "Strategy": view["strategy"] or "",
                "Provider": view["quote_provider"] or "Unknown",
                "Data Status": data_label,
                "Holding Score": h_score,
                "Action": "HOLD",
            }
        )

    action_queue = [
        {
            "Action": f"{row['Action']} {row['Symbol']}",
            "Why": row["Catalyst"],
            "Confidence": row["Confidence"],
            "Tier": row["Tier"],
            "Risk": row["Risk/Reward"],
            "Data Status": row["Data Status"],
        }
        for row in best[:10]
    ]

    sectors: dict[str, dict[str, Any]] = {}
    for row in best:
        sector = str(next((c.get("sector") for c in candidates if str(c.get("symbol") or "").upper() == row["Symbol"]), "") or "Unknown")
        bucket = sectors.setdefault(sector, {"Sector": sector, "Trend": "NEUTRAL", "Relative Strength": 0.0, "Momentum": 0.0, "Oracle Exposure": "$0.00", "Qualified Candidates": 0})
        bucket["Qualified Candidates"] += 1
        bucket["Relative Strength"] = max(bucket["Relative Strength"], as_float(row["Mover Score"]))
        bucket["Momentum"] = max(bucket["Momentum"], as_float(row["Trend/Momentum"]))

    profit_sources = []
    for row in ledger_rows:
        if not is_allowed_us_stock(row):
            continue
        realized = as_float(row.get("net_pnl"))
        profit_sources.append(
            {
                "Symbol": str(row.get("symbol") or "").upper(),
                "Strategy": row.get("strategy") or "",
                "Entry": money_text(row.get("entry_price")),
                "Exit / Current": money_text(row.get("exit_price") or row.get("current_price")),
                "Qty": format_quantity(row.get("quantity")),
                "Realized P/L": signed_money_text(realized),
                "Unrealized P/L": row.get("unrealized_pnl") if row.get("unrealized_pnl") is not None else "WAITING FOR VERIFIED PRICE",
                "Net Contribution": signed_money_text(realized + as_float(row.get("unrealized_pnl"))),
            }
        )

    rotations = []
    tactical_owned = [row for row in owned if row.get("Bucket") == "TACTICAL"]
    if tactical_owned and best:
        weakest = min(tactical_owned, key=lambda row: as_float(row.get("Holding Score"), 50.0))
        strongest = best[0]
        improvement = as_float(strongest.get("Mover Score")) - as_float(weakest.get("Holding Score"))
        if should_rotate(incoming_score=strongest.get("Mover Score"), held_score=weakest.get("Holding Score")):
            rotations.append(
                {
                    "current_holding": weakest["Symbol"],
                    "holding_score": weakest["Holding Score"],
                    "incoming_candidate": strongest["Symbol"],
                    "candidate_score": strongest["Mover Score"],
                    "score_improvement": round(improvement, 1),
                    "recommended_action": "ROTATE INTO",
                }
            )

    return {
        "best_trades": best,
        "movers": sorted(movers, key=lambda row: as_float(row.get("Mover Score")), reverse=True)[:15],
        "action_queue": action_queue,
        "owned": owned,
        "rotations": rotations,
        "profit_sources": profit_sources,
        "sectors": sorted(sectors.values(), key=lambda row: as_float(row.get("Relative Strength")), reverse=True),
        "rejected": rejected[:20],
    }


def trade_summary(trades: list[dict[str, Any]]) -> dict[str, Any]:
    buys = [trade for trade in trades if str(trade.get("side") or "").upper() == "BUY"]
    sells = [trade for trade in trades if str(trade.get("side") or "").upper() == "SELL"]
    return {
        "Total Trades": len(trades),
        "Buys": len(buys),
        "Sells": len(sells),
        "Realized P/L": sum(as_float(trade.get("realized_pnl")) for trade in trades),
        "Trade Volume": sum(as_float(trade.get("value")) for trade in trades),
    }
