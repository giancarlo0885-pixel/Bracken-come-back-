from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass
from typing import Any

from config import (
    CRYPTO_MIN_CASH_RESERVE_PCT,
    ENABLE_FRACTIONAL_CRYPTO,
    ENABLE_FRACTIONAL_EQUITIES,
    LARGE_ACCOUNT_THRESHOLD,
    MARKET_REGIME_SIZE_MULTIPLIERS,
    MAX_POSITION_VS_DAILY_DOLLAR_VOLUME_PCT,
    MAX_PORTFOLIO_RISK_PER_TRADE,
    MAX_SINGLE_CRYPTO_TACTICAL_POSITION_PCT,
    MAX_SINGLE_STOCK_POSITION_PCT,
    MAX_TOTAL_DEPLOYED_PCT,
    MIN_TRADE_NOTIONAL,
    PAPER_BROKER_MODE,
    PAPER_MAX_MARGIN_UTILIZATION_PCT,
    SMALL_ACCOUNT_THRESHOLD,
    STOCK_MIN_CASH_RESERVE_PCT,
    TIER_SIZE_MULTIPLIERS,
)

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


@dataclass(frozen=True)
class CapitalAllocationDecision:
    symbol: str
    market: str
    validated_equity: float
    risk_budget_dollars: float
    max_position_dollars: float
    calculated_notional: float
    calculated_quantity: float
    tier_multiplier: float
    confidence_multiplier: float
    regime_multiplier: float
    liquidity_multiplier: float
    drawdown_multiplier: float
    cash_after_trade: float
    reserve_required: float
    approved: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def risk_based_position_notional(
    *,
    equity: float,
    price: float,
    stop_price: float,
    max_risk_pct: float,
    max_position_pct: float,
    tier_multiplier: float,
) -> float:
    equity = _num(equity)
    price = _num(price)
    stop_price = _num(stop_price)
    if equity <= 0 or price <= 0 or stop_price <= 0:
        return 0.0
    stop_distance_pct = abs(price - stop_price) / price
    if stop_distance_pct <= 0:
        return 0.0
    risk_budget = equity * max_risk_pct * tier_multiplier
    risk_limited_notional = risk_budget / stop_distance_pct
    concentration_limited_notional = equity * max_position_pct
    return max(0.0, min(risk_limited_notional, concentration_limited_notional))


def confidence_multiplier(confidence: float) -> float:
    confidence = _num(confidence)
    if confidence <= 1:
        confidence *= 100
    if confidence >= 90:
        return 1.00
    if confidence >= 80:
        return 0.90
    if confidence >= 70:
        return 0.70
    if confidence >= 62:
        return 0.45
    return 0.0


def liquidity_multiplier(dollar_volume: float) -> float:
    dollar_volume = _num(dollar_volume)
    if dollar_volume >= 1_000_000_000:
        return 1.00
    if dollar_volume >= 250_000_000:
        return 0.85
    if dollar_volume >= 50_000_000:
        return 0.60
    if dollar_volume >= 20_000_000:
        return 0.40
    return 0.0


def drawdown_risk_multiplier(drawdown_pct: float) -> float:
    drawdown = abs(_num(drawdown_pct))
    if drawdown < 0.05:
        return 1.00
    if drawdown < 0.10:
        return 0.75
    if drawdown < 0.15:
        return 0.50
    if drawdown < 0.20:
        return 0.25
    return 0.0


def max_positions_for_equity(equity: float) -> int:
    equity = _num(equity)
    if equity < 250:
        return 3
    if equity < 1_000:
        return 5
    if equity < 5_000:
        return 8
    if equity < 25_000:
        return 12
    if equity < 100_000:
        return 16
    return 20


def estimated_slippage_pct(*, spread_pct: float, notional: float, daily_dollar_volume: float) -> float:
    spread = max(0.0, _num(spread_pct))
    notional = max(0.0, _num(notional))
    daily = max(0.0, _num(daily_dollar_volume))
    participation = notional / daily if daily > 0 else 1.0
    return max(spread / 2.0, min(0.02, participation * 5.0))


def adaptive_capital_allocation(
    *,
    symbol: str,
    market: str,
    equity: float,
    cash: float,
    current_exposure: float,
    price: float,
    stop_price: float,
    tier: str,
    confidence: float,
    reward_risk: float,
    market_regime: str,
    dollar_volume: float,
    spread_pct: float = 0.0,
    drawdown_pct: float = 0.0,
    existing_position_value: float = 0.0,
    buying_power: float | None = None,
    buying_power_validated: bool = False,
    fractional_equities: bool | None = None,
    fractional_crypto: bool | None = None,
) -> CapitalAllocationDecision:
    market = str(market or "cash").lower()
    equity = max(0.0, _num(equity))
    cash = max(0.0, _num(cash))
    price = _num(price)
    stop_price = _num(stop_price)
    if equity <= 0 or cash <= 0 or price <= 0 or stop_price <= 0:
        return CapitalAllocationDecision(symbol, market, equity, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, cash, 0.0, False, "BROKER_CAPACITY_INVALID")

    is_crypto = market == "crypto" or str(symbol).upper().endswith("-USD")
    reserve_pct = CRYPTO_MIN_CASH_RESERVE_PCT if is_crypto else STOCK_MIN_CASH_RESERVE_PCT
    max_position_pct = MAX_SINGLE_CRYPTO_TACTICAL_POSITION_PCT if is_crypto else MAX_SINGLE_STOCK_POSITION_PCT
    fractional_allowed = ENABLE_FRACTIONAL_CRYPTO if is_crypto else ENABLE_FRACTIONAL_EQUITIES
    if fractional_crypto is not None and is_crypto:
        fractional_allowed = fractional_crypto
    if fractional_equities is not None and not is_crypto:
        fractional_allowed = fractional_equities

    tier_mult = TIER_SIZE_MULTIPLIERS.get(str(tier or "").upper(), 0.0)
    confidence_mult = confidence_multiplier(confidence)
    regime_mult = MARKET_REGIME_SIZE_MULTIPLIERS.get(str(market_regime or "neutral").lower(), 0.60)
    liquid_mult = liquidity_multiplier(dollar_volume)
    drawdown_mult = drawdown_risk_multiplier(drawdown_pct)
    if tier_mult <= 0 or confidence_mult <= 0 or liquid_mult <= 0 or drawdown_mult <= 0:
        reason = "SEVERE_DRAWDOWN_BLOCKS_NEW_TRADE" if drawdown_mult <= 0 else "QUALITY_OR_LIQUIDITY_BELOW_THRESHOLD"
        return CapitalAllocationDecision(symbol, market, equity, 0.0, equity * max_position_pct, 0.0, 0.0, tier_mult, confidence_mult, regime_mult, liquid_mult, drawdown_mult, cash, equity * reserve_pct, False, reason)
    if _num(reward_risk) < 1.25:
        return CapitalAllocationDecision(symbol, market, equity, 0.0, equity * max_position_pct, 0.0, 0.0, tier_mult, confidence_mult, regime_mult, liquid_mult, drawdown_mult, cash, equity * reserve_pct, False, "REWARD_RISK_BELOW_MINIMUM")

    base_notional = risk_based_position_notional(
        equity=equity,
        price=price,
        stop_price=stop_price,
        max_risk_pct=MAX_PORTFOLIO_RISK_PER_TRADE,
        max_position_pct=max_position_pct,
        tier_multiplier=tier_mult,
    )
    base_risk_budget = equity * MAX_PORTFOLIO_RISK_PER_TRADE
    adjusted_risk_budget = base_risk_budget * tier_mult * confidence_mult * regime_mult * liquid_mult * drawdown_mult
    adjusted_notional = base_notional * confidence_mult * regime_mult * liquid_mult * drawdown_mult
    reserve_required = equity * reserve_pct
    spendable_cash = max(0.0, cash - reserve_required)
    if buying_power_validated and buying_power is not None:
        spendable_cash = min(spendable_cash, max(0.0, _num(buying_power)))
    available_exposure_room = max(0.0, equity * MAX_TOTAL_DEPLOYED_PCT - max(0.0, _num(current_exposure)))
    single_position_room = max(0.0, equity * max_position_pct - max(0.0, _num(existing_position_value)))
    participation_cap = max(0.0, _num(dollar_volume)) * MAX_POSITION_VS_DAILY_DOLLAR_VOLUME_PCT
    if equity < SMALL_ACCOUNT_THRESHOLD:
        adjusted_notional *= 0.70
    elif equity >= LARGE_ACCOUNT_THRESHOLD:
        adjusted_notional = min(adjusted_notional, participation_cap)

    notional = max(0.0, min(adjusted_notional, spendable_cash, available_exposure_room, single_position_room, participation_cap))
    if notional < MIN_TRADE_NOTIONAL:
        return CapitalAllocationDecision(symbol, market, equity, round(adjusted_risk_budget, 2), round(equity * max_position_pct, 2), 0.0, 0.0, tier_mult, confidence_mult, regime_mult, liquid_mult, drawdown_mult, cash, round(reserve_required, 2), False, "BELOW_MINIMUM_NOTIONAL")

    quantity = notional / price
    if not fractional_allowed:
        quantity = math.floor(quantity)
        notional = quantity * price
        if quantity <= 0 or notional < MIN_TRADE_NOTIONAL:
            return CapitalAllocationDecision(symbol, market, equity, round(adjusted_risk_budget, 2), round(equity * max_position_pct, 2), 0.0, 0.0, tier_mult, confidence_mult, regime_mult, liquid_mult, drawdown_mult, cash, round(reserve_required, 2), False, "BELOW_MINIMUM_NOTIONAL")

    cash_after = cash - notional
    if cash_after < reserve_required:
        return CapitalAllocationDecision(symbol, market, equity, round(adjusted_risk_budget, 2), round(equity * max_position_pct, 2), 0.0, 0.0, tier_mult, confidence_mult, regime_mult, liquid_mult, drawdown_mult, cash_after, round(reserve_required, 2), False, "CASH_RESERVE_PROTECTED")
    slippage = estimated_slippage_pct(spread_pct=spread_pct, notional=notional, daily_dollar_volume=dollar_volume)
    reason = (
        f"Base risk ${base_risk_budget:,.2f}; tier {str(tier).upper()} x{tier_mult:.2f}; "
        f"confidence x{confidence_mult:.2f}; regime x{regime_mult:.2f}; "
        f"liquidity x{liquid_mult:.2f}; drawdown x{drawdown_mult:.2f}; "
        f"final risk budget ${adjusted_risk_budget:,.2f}; estimated slippage {slippage:.2%}."
    )
    return CapitalAllocationDecision(
        symbol=str(symbol).upper(),
        market=market,
        validated_equity=round(equity, 2),
        risk_budget_dollars=round(adjusted_risk_budget, 2),
        max_position_dollars=round(equity * max_position_pct, 2),
        calculated_notional=round(notional, 2),
        calculated_quantity=round(quantity, 10),
        tier_multiplier=tier_mult,
        confidence_multiplier=confidence_mult,
        regime_multiplier=regime_mult,
        liquidity_multiplier=liquid_mult,
        drawdown_multiplier=drawdown_mult,
        cash_after_trade=round(cash_after, 2),
        reserve_required=round(reserve_required, 2),
        approved=True,
        reason=reason,
    )


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
