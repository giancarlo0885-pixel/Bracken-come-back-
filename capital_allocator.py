from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass
from typing import Any

from config import PAPER_BROKER_MODE, PAPER_MAX_MARGIN_UTILIZATION_PCT

MAX_SINGLE_POSITION_PCT = min(0.25, max(0.02, float(os.getenv("CAPITAL_MAX_SINGLE_POSITION_PCT", "0.08"))))
MAX_SECTOR_EXPOSURE_PCT = min(0.65, max(0.10, float(os.getenv("CAPITAL_MAX_SECTOR_EXPOSURE_PCT", "0.35"))))
TARGET_CASH_RESERVE_PCT = min(0.50, max(0.02, float(os.getenv("CAPITAL_TARGET_CASH_RESERVE_PCT", "0.05"))))
MAX_CORRELATION = min(0.99, max(0.20, float(os.getenv("CAPITAL_MAX_CORRELATION", "0.88"))))
MIN_ROTATION_EDGE = max(1.0, float(os.getenv("CAPITAL_MIN_ROTATION_EDGE", "6.0")))
AGGRESSIVE_TRADING = os.getenv("AGGRESSIVE_TRADING", "true").lower() == "true"
MIN_CAPITAL_PRIORITY = float(os.getenv("CAPITAL_MIN_PRIORITY", "50" if AGGRESSIVE_TRADING else "55"))
MIN_TRADE_PCT = float(os.getenv("CAPITAL_MIN_TRADE_PCT", "0.015" if AGGRESSIVE_TRADING else "0.0"))


def _num(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return default if not math.isfinite(result) else result
    except (TypeError, ValueError):
        return default


def _value(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class CapitalAllocationAssessment:
    portfolio_fit_score: float
    capital_priority_score: float
    recommended_position_pct: float
    recommended_position_value: float
    cash_after_trade_pct: float
    concentration_penalty: float
    correlation_penalty: float
    liquidity_multiplier: float
    regime_multiplier: float
    edge_multiplier: float
    final_multiplier: float
    rotation_candidate: str | None
    rotation_edge: float
    approved: bool
    veto: bool
    verdict: str
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_capital_allocation(
    signal: Any,
    *,
    decision: Any,
    portfolio: dict[str, Any] | None = None,
    positions: list[dict[str, Any]] | None = None,
    competing_opportunities: list[dict[str, Any]] | None = None,
) -> CapitalAllocationAssessment:
    """Allocate capital after quant, memory, and scenario approval.

    This layer does not predict returns. It decides whether the opportunity is
    worthy of scarce portfolio capital and whether an existing weak position
    should be rotated out first.
    """
    portfolio = portfolio or {}
    positions = positions or []
    competing_opportunities = competing_opportunities or []

    equity = max(0.01, _num(portfolio.get("equity", portfolio.get("total_equity", 0.0)), 0.0))
    cash = max(0.0, _num(portfolio.get("cash", equity), equity))
    buying_power = max(cash, _num(portfolio.get("buying_power", cash), cash))
    gross_exposure = max(0.0, _num(portfolio.get("gross_exposure", 0.0), 0.0))
    leverage_limit = max(1.0, _num(portfolio.get("leverage_limit", 1.0), 1.0))
    margin_utilization = _clip(
        _num(portfolio.get("margin_utilization_pct", 0.0), 0.0) / 100.0,
        0.0,
        2.0,
    )
    cash_pct = _clip(cash / equity, 0.0, 1.0)

    quality = _num(_value(decision, "opportunity_score", 0.0))
    probability = _num(_value(decision, "probability_of_profit", 0.0)) / 100.0
    rr = max(0.0, _num(_value(decision, "risk_reward_ratio", 0.0)))
    recommendation = str(_value(decision, "recommendation", "WATCH")).upper()
    quant = _value(decision, "quant", {}) or {}
    scenario = _value(decision, "scenario", {}) or {}

    net_ev = _num(_value(quant, "net_expected_value_pct", 0.0))
    execution = _num(_value(quant, "execution_score", 50.0), 50.0)
    risk = _num(_value(quant, "risk_score", 50.0), 50.0)
    scenario_mult = _num(_value(scenario, "position_multiplier", 1.0), 1.0)

    symbol = str(_value(signal, "symbol", ""))
    sector = str(_value(signal, "sector", "UNKNOWN") or "UNKNOWN").upper()
    regime = str(_value(signal, "regime", "neutral") or "neutral").lower()
    estimated_corr = abs(_num(_value(signal, "portfolio_correlation", 0.35), 0.35))

    invested_value = 0.0
    symbol_value = 0.0
    sector_value = 0.0
    weakest_symbol: str | None = None
    weakest_score = 101.0
    for pos in positions:
        value = _num(pos.get("market_value"), _num(pos.get("quantity")) * _num(pos.get("current_price")))
        invested_value += max(0.0, value)
        if str(pos.get("symbol", "")) == symbol:
            symbol_value += max(0.0, value)
        if str(pos.get("sector", "UNKNOWN") or "UNKNOWN").upper() == sector:
            sector_value += max(0.0, value)
        held_score = _num(pos.get("opportunity_score", pos.get("score", 50.0)), 50.0)
        if held_score < weakest_score:
            weakest_score = held_score
            weakest_symbol = str(pos.get("symbol", "")) or None

    current_symbol_pct = symbol_value / equity
    current_sector_pct = sector_value / equity
    concentration_penalty = _clip(
        max(0.0, current_symbol_pct - MAX_SINGLE_POSITION_PCT * 0.65) * 170
        + max(0.0, current_sector_pct - MAX_SECTOR_EXPOSURE_PCT * 0.70) * 110,
        0.0,
        45.0,
    )
    correlation_penalty = _clip(max(0.0, estimated_corr - 0.55) * 90.0, 0.0, 35.0)

    regime_multiplier = {
        "bull": 1.08, "risk-on": 1.08, "neutral": 0.95, "sideways": 0.80,
        "bear": 0.60, "risk-off": 0.55, "crisis": 0.35,
    }.get(regime, 0.90)
    liquidity_multiplier = _clip((execution / 100.0) ** 0.75, 0.35, 1.05)
    edge_multiplier = _clip(
        0.30 + quality / 130.0 + probability * 0.45 + min(rr, 4.0) * 0.07 + max(0.0, net_ev) * 8.0,
        0.35,
        1.35,
    )

    portfolio_fit = _clip(
        0.36 * risk + 0.28 * execution + 0.20 * quality + 0.16 * min(100.0, probability * 100.0)
        - concentration_penalty - correlation_penalty,
        0.0,
        100.0,
    )

    competitor_best = max((_num(x.get("opportunity_score")) for x in competing_opportunities if str(x.get("symbol", "")) != symbol), default=0.0)
    relative_priority = _clip(50.0 + (quality - competitor_best) * 2.0, 0.0, 100.0) if competitor_best else quality
    capital_priority = _clip(0.70 * portfolio_fit + 0.30 * relative_priority, 0.0, 100.0)

    base_position_pct = _clip(0.025 + max(0.0, quality - 68.0) / 220.0 + max(0.0, probability - 0.50) * 0.12, 0.02, MAX_SINGLE_POSITION_PCT)
    concentration_multiplier = _clip(1.0 - concentration_penalty / 55.0, 0.20, 1.0)
    correlation_multiplier = _clip(1.0 - correlation_penalty / 45.0, 0.25, 1.0)
    cash_multiplier = _clip((cash_pct - TARGET_CASH_RESERVE_PCT) / max(0.05, 1.0 - TARGET_CASH_RESERVE_PCT), 0.0, 1.0)

    final_multiplier = _clip(
        scenario_mult * regime_multiplier * liquidity_multiplier * edge_multiplier
        * concentration_multiplier * correlation_multiplier * max(0.35 if AGGRESSIVE_TRADING else 0.20, cash_multiplier),
        0.0,
        1.35,
    )
    recommended_position_pct = min(MAX_SINGLE_POSITION_PCT - current_symbol_pct, base_position_pct * final_multiplier)
    recommended_position_pct = max(0.0, recommended_position_pct)
    # V23 breathing room: a valid BUY with positive edge receives a small starter
    # allocation instead of being mathematically rounded down to zero. Hard risk
    # limits below still retain final veto authority.
    if (
        AGGRESSIVE_TRADING
        and recommendation == "BUY"
        and net_ev > 0
        and probability >= 0.52
        and execution >= 55
        and current_symbol_pct < MAX_SINGLE_POSITION_PCT
    ):
        recommended_position_pct = max(recommended_position_pct, min(MIN_TRADE_PCT, MAX_SINGLE_POSITION_PCT - current_symbol_pct))
    recommended_value = min(buying_power, equity * recommended_position_pct)
    reserve_cash = equity * TARGET_CASH_RESERVE_PCT
    cash_spend = min(max(0.0, cash - reserve_cash), recommended_value)
    cash_after_pct = _clip((cash - cash_spend) / equity, 0.0, 1.0)

    rotation_edge = quality - weakest_score if weakest_symbol else 0.0
    rotation_candidate = weakest_symbol if weakest_symbol and rotation_edge >= MIN_ROTATION_EDGE else None

    hard_concentration = current_symbol_pct >= MAX_SINGLE_POSITION_PCT or current_sector_pct >= MAX_SECTOR_EXPOSURE_PCT
    hard_correlation = estimated_corr > MAX_CORRELATION and current_sector_pct > 0.20
    insufficient_funds = recommended_value <= 0.0 or buying_power < recommended_value
    margin_overloaded = bool(
        PAPER_BROKER_MODE
        and (margin_utilization >= PAPER_MAX_MARGIN_UTILIZATION_PCT or gross_exposure >= equity * leverage_limit * PAPER_MAX_MARGIN_UTILIZATION_PCT)
    )
    reserve_breach = bool(
        not PAPER_BROKER_MODE
        and cash_after_pct < TARGET_CASH_RESERVE_PCT * (0.35 if AGGRESSIVE_TRADING else 0.55)
    )
    veto = (
        recommendation != "BUY" or hard_concentration or hard_correlation
        or insufficient_funds or margin_overloaded or reserve_breach
        or capital_priority < MIN_CAPITAL_PRIORITY
    )
    approved = not veto
    verdict = "ALLOCATE" if approved and capital_priority >= 78 else ("SMALL ALLOCATION" if approved else ("ROTATE FIRST" if rotation_candidate else "DO NOT ALLOCATE"))
    summary = (
        f"{verdict}: priority {capital_priority:.1f}/100, portfolio fit {portfolio_fit:.1f}/100, "
        f"target {recommended_position_pct:.1%} of equity (${recommended_value:,.2f}), "
        f"cash reserve after trade {cash_after_pct:.1%}, "
        f"paper buying power ${buying_power:,.2f}."
    )
    if rotation_candidate:
        summary += f" Rotation candidate: {rotation_candidate} with a {rotation_edge:.1f}-point edge."

    return CapitalAllocationAssessment(
        round(portfolio_fit, 2), round(capital_priority, 2), round(recommended_position_pct * 100.0, 2),
        round(recommended_value, 2), round(cash_after_pct * 100.0, 2), round(concentration_penalty, 2),
        round(correlation_penalty, 2), round(liquidity_multiplier, 4), round(regime_multiplier, 4),
        round(edge_multiplier, 4), round(final_multiplier, 4), rotation_candidate, round(rotation_edge, 2),
        approved, veto, verdict, summary,
    )
