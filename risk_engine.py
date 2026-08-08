from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Any

from config import (
    ENABLE_AUTOMATED_EXITS,
    ENABLE_AUTOTRADE,
    ENABLE_BROKER_SUBMISSION,
    ENABLE_CRYPTO_AUTOTRADE,
    ENABLE_NEW_ENTRIES,
    ENABLE_PORTFOLIO_ROTATION,
    ENABLE_STOCK_AUTOTRADE,
    GLOBAL_KILL_SWITCH,
    MAX_DAILY_DRAWDOWN_PCT,
    MAX_DAILY_TURNOVER_PCT,
    MAX_NEW_ENTRIES_PER_DAY,
    MAX_OPEN_POSITIONS,
    MAX_POSITION_FRACTION,
    MAX_WEEKLY_LOSS_PCT,
    MIN_CASH_RESERVE_PCT,
    QUANT_MAX_SLIPPAGE_PCT,
    QUANT_MAX_SPREAD_PCT,
)
from execution_policy import execution_policy
from market_sessions import quote_is_fresh
from provider_router import normalize_symbol


class RiskState(str, Enum):
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    REDUCE_RISK = "REDUCE RISK"
    HALTED = "HALTED"
    MANUAL_REVIEW = "MANUAL REVIEW"


@dataclass
class ExecutionSwitches:
    autotrade: bool = ENABLE_AUTOTRADE
    stock_autotrade: bool = ENABLE_STOCK_AUTOTRADE
    crypto_autotrade: bool = ENABLE_CRYPTO_AUTOTRADE
    new_entries: bool = ENABLE_NEW_ENTRIES
    automated_exits: bool = ENABLE_AUTOMATED_EXITS
    portfolio_rotation: bool = ENABLE_PORTFOLIO_ROTATION
    broker_submission: bool = ENABLE_BROKER_SUBMISSION
    global_kill_switch: bool = GLOBAL_KILL_SWITCH

    def execution_allowed(self, market: str, intent: str) -> bool:
        return execution_policy(
            market=market,
            intent=intent,
            overrides={
                "ENABLE_AUTOTRADE": self.autotrade,
                "ENABLE_STOCK_AUTOTRADE": self.stock_autotrade,
                "ENABLE_CRYPTO_AUTOTRADE": self.crypto_autotrade,
                "ENABLE_NEW_ENTRIES": self.new_entries,
                "ENABLE_AUTOMATED_EXITS": self.automated_exits,
                "ENABLE_PORTFOLIO_ROTATION": self.portfolio_rotation,
                "ENABLE_BROKER_SUBMISSION": self.broker_submission,
                "GLOBAL_KILL_SWITCH": self.global_kill_switch,
            },
        ).allowed


@dataclass
class RiskCheckResult:
    approved: bool
    state: RiskState
    checks: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    reason: str = ""
    metrics: dict[str, float] = field(default_factory=dict)

    def add(self, name: str, passed: bool, detail: str) -> None:
        self.checks.append({"name": name, "passed": bool(passed), "detail": detail})
        if not passed and self.approved:
            self.approved = False
            self.reason = detail

    def warn(self, name: str, detail: str) -> None:
        self.checks.append({"name": name, "passed": True, "detail": detail, "warning": True})
        self.warnings.append(detail)

    @property
    def allowed(self) -> bool:
        return self.approved

    @property
    def reasons(self) -> list[str]:
        return [item["detail"] for item in self.checks if not item.get("passed")]


def _finite(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _quote_identity_ok(symbol: str, quote: dict[str, Any]) -> bool:
    requested = normalize_symbol(symbol)
    return (
        normalize_symbol(quote.get("symbol") or requested) == requested
        and normalize_symbol(quote.get("requested_symbol")) == requested
        and normalize_symbol(quote.get("provider_symbol")) == requested
        and quote.get("quote_verified") is True
    )


def pre_trade_risk_checks(
    *,
    market: str,
    symbol: str,
    side: str,
    order_value: float,
    portfolio_equity: float,
    cash: float,
    quote: dict[str, Any],
    positions: list[dict[str, Any]] | None = None,
    daily_loss_pct: float | None = None,
    weekly_loss_pct: float | None = None,
    spread_pct: float | None = None,
    slippage_pct: float | None = None,
    liquidity_value: float | None = None,
    correlation_exposure_pct: float | None = None,
    concentration_pct: float | None = None,
    new_entries_today: int | None = None,
    turnover_pct_today: float | None = None,
    switches: ExecutionSwitches | None = None,
    intent: str | None = None,
    leverage_used: float | None = None,
    margin_utilization_pct: float | None = None,
) -> RiskCheckResult:
    switches = switches or ExecutionSwitches()
    intent = intent or ("exit" if str(side).upper() == "SELL" else "entry")
    result = RiskCheckResult(True, RiskState.NORMAL)
    result.add("execution_switch", switches.execution_allowed(market, intent), f"{intent} execution is disabled")
    result.add("quote_identity", _quote_identity_ok(symbol, quote), "quote identity is not verified")
    result.add("quote_freshness", quote_is_fresh(quote.get("quote_timestamp"), str(quote.get("interval") or "1d"), symbol=symbol), "quote is stale")
    forced_exit = intent == "forced_risk_reduction"
    sell_intents = {"exit", "forced_risk_reduction", "rotation_out"}
    entry_intents = {"entry", "rotation_in"}
    required = {
        "price": quote.get("price"),
        "order_value": order_value,
        "cash": cash,
        "equity": portfolio_equity,
        "leverage_used": leverage_used,
        "margin_utilization_pct": margin_utilization_pct,
    }
    if intent in entry_intents:
        required.update(
            {
                "daily_loss_pct": daily_loss_pct,
                "weekly_loss_pct": weekly_loss_pct,
                "spread_pct": spread_pct,
                "slippage_pct": slippage_pct,
                "liquidity_value": liquidity_value,
                "correlation_exposure_pct": correlation_exposure_pct,
                "concentration_pct": concentration_pct,
                "new_entries_today": new_entries_today,
                "turnover_pct_today": turnover_pct_today,
            }
        )
    elif intent in sell_intents:
        required.update({})
    for name, value in required.items():
        finite = _finite(value)
        if finite is None:
            label = name.removesuffix("_pct").replace("_", " ")
            result.add(f"finite_{name}", False, f"{label} metric unavailable or non-finite")
        else:
            result.metrics[name] = finite
    optional_metrics = {
        "daily_loss_pct": daily_loss_pct,
        "weekly_loss_pct": weekly_loss_pct,
        "spread_pct": spread_pct,
        "slippage_pct": slippage_pct,
        "liquidity_value": liquidity_value,
        "correlation_exposure_pct": correlation_exposure_pct,
        "concentration_pct": concentration_pct,
        "new_entries_today": new_entries_today,
        "turnover_pct_today": turnover_pct_today,
    }
    for name, value in optional_metrics.items():
        if name in result.metrics:
            continue
        finite = _finite(value)
        if finite is not None:
            result.metrics[name] = finite
    if not result.approved:
        result.state = RiskState.MANUAL_REVIEW
        return result
    daily_loss = result.metrics.get("daily_loss_pct", 0.0)
    weekly_loss = result.metrics.get("weekly_loss_pct", 0.0)
    spread = result.metrics.get("spread_pct")
    slippage = result.metrics.get("slippage_pct")
    liquidity = result.metrics.get("liquidity_value", 0.0)
    concentration = result.metrics.get("concentration_pct", 0.0)
    correlation = result.metrics.get("correlation_exposure_pct", 0.0)
    entries_today = int(result.metrics.get("new_entries_today", 0))
    turnover = result.metrics.get("turnover_pct_today", 0.0)
    if forced_exit:
        for name, failed, detail in (
            ("daily_loss", daily_loss > MAX_DAILY_DRAWDOWN_PCT, "daily loss limit already exceeded"),
            ("weekly_loss", weekly_loss > MAX_WEEKLY_LOSS_PCT, "weekly loss limit already exceeded"),
            ("turnover", turnover > MAX_DAILY_TURNOVER_PCT, "maximum daily turnover already exceeded"),
            ("concentration", concentration > MAX_POSITION_FRACTION, "concentration already excessive"),
            ("correlation", correlation > 0.35, "correlation exposure already excessive"),
            ("spread", spread is not None and spread > QUANT_MAX_SPREAD_PCT, "spread exceeds maximum during risk reduction"),
            ("slippage", slippage is not None and slippage > QUANT_MAX_SLIPPAGE_PCT, "slippage exceeds maximum during risk reduction"),
        ):
            if failed:
                result.warn(name, detail)
    elif intent in entry_intents:
        result.add("daily_loss", daily_loss <= MAX_DAILY_DRAWDOWN_PCT, "daily loss limit reached")
        result.add("weekly_loss", weekly_loss <= MAX_WEEKLY_LOSS_PCT, "weekly loss limit reached")
        result.add("spread", spread is not None and spread <= QUANT_MAX_SPREAD_PCT, "spread exceeds maximum")
        result.add("slippage", slippage is not None and slippage <= QUANT_MAX_SLIPPAGE_PCT, "slippage exceeds maximum")
        result.add("turnover", turnover <= MAX_DAILY_TURNOVER_PCT, "maximum daily turnover reached")
        result.add("concentration", concentration <= MAX_POSITION_FRACTION, "concentration limit exceeded")
        result.add("correlation", correlation <= 0.35, "correlation exposure limit exceeded")
    result.add("order_value", forced_exit or portfolio_equity > 0 and order_value / portfolio_equity <= MAX_POSITION_FRACTION, "order value exceeds maximum position size")
    result.add("cash_reserve", forced_exit or side.upper() == "SELL" or cash - order_value >= portfolio_equity * MIN_CASH_RESERVE_PCT, "minimum cash reserve would be breached")
    result.add("liquidity", forced_exit or intent not in entry_intents or order_value <= liquidity * 0.01, "order exceeds liquidity limit")
    result.add("new_entries", intent not in entry_intents or entries_today < MAX_NEW_ENTRIES_PER_DAY, "maximum new entries reached")
    if positions is not None:
        result.add("open_positions", intent not in entry_intents or len(positions) < MAX_OPEN_POSITIONS, "maximum open positions reached")
    if not result.approved:
        result.state = RiskState.HALTED if "disabled" in result.reason or "loss limit" in result.reason else RiskState.MANUAL_REVIEW
    return result


def risk_event_payload(event: str, market: str, symbol: str, result: RiskCheckResult) -> dict[str, Any]:
    return {
        "event": event,
        "market": market,
        "symbol": normalize_symbol(symbol),
        "state": result.state.value,
        "approved": result.approved,
        "reason": result.reason,
        "checks": result.checks,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
