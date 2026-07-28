from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from config import PAPER_BROKER_MODE, PAPER_MAX_MARGIN_UTILIZATION_PCT


def _num(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return default if not math.isfinite(result) else result
    except (TypeError, ValueError):
        return default


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _position_value(position: dict[str, Any]) -> float:
    return max(0.0, _num(position.get("market_value"), _num(position.get("quantity")) * _num(position.get("current_price", position.get("entry_price")))))


@dataclass(frozen=True)
class PortfolioSupercomputerAssessment:
    portfolio_health_score: float
    diversification_score: float
    liquidity_score: float
    concentration_score: float
    drawdown_resilience_score: float
    candidate_fit_score: float
    current_cash_pct: float
    projected_cash_pct: float
    current_largest_position_pct: float
    projected_largest_position_pct: float
    current_sector_concentration_pct: float
    projected_sector_concentration_pct: float
    recommended_trade_value: float
    recommended_trade_pct: float
    weakest_position: str | None
    weakest_position_score: float
    rotation_candidate: str | None
    rebalance_actions: tuple[str, ...]
    position_multiplier: float
    approved: bool
    veto: bool
    verdict: str
    summary: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["rebalance_actions"] = list(self.rebalance_actions)
        return data


def assess_portfolio_supercomputer(
    signal: Any,
    *,
    decision: dict[str, Any],
    portfolio: dict[str, Any] | None = None,
    positions: list[dict[str, Any]] | None = None,
) -> PortfolioSupercomputerAssessment:
    """Portfolio-level final gate and target allocator.

    The assessment is deterministic and only uses supplied portfolio and signal
    evidence. It never invents correlations or fundamentals.
    """
    portfolio = portfolio or {}
    positions = positions or []
    equity = max(0.01, _num(portfolio.get("equity", portfolio.get("total_equity", 0.0)), 0.0))
    cash = max(0.0, _num(portfolio.get("cash", equity), equity))
    buying_power = max(cash, _num(portfolio.get("buying_power", cash), cash))
    gross_exposure = max(0.0, _num(portfolio.get("gross_exposure", 0.0), 0.0))
    leverage_limit = max(1.0, _num(portfolio.get("leverage_limit", 1.0), 1.0))
    margin_utilization_pct = _clip(
        _num(portfolio.get("margin_utilization_pct", 0.0), 0.0),
        0.0,
        999.0,
    )
    excess_liquidity = _num(portfolio.get("excess_liquidity", equity), equity)
    cash_pct = _clip(cash / equity, 0.0, 1.0)

    symbol = str(signal.get("symbol", "") if isinstance(signal, dict) else getattr(signal, "symbol", "")).upper()
    sector = str(signal.get("sector", "UNKNOWN") if isinstance(signal, dict) else getattr(signal, "sector", "UNKNOWN") or "UNKNOWN").upper()
    candidate_corr = abs(_num(signal.get("portfolio_correlation", 0.35) if isinstance(signal, dict) else getattr(signal, "portfolio_correlation", 0.35), 0.35))

    capital = decision.get("capital", {}) or {}
    scenario = decision.get("scenario", {}) or {}
    quant = decision.get("quant", {}) or {}
    quality = _num(decision.get("opportunity_score"))
    probability = _num(decision.get("probability_of_profit"))
    rr = _num(decision.get("risk_reward_ratio"))
    recommendation = str(decision.get("recommendation", "WATCH")).upper()

    values: list[tuple[str, str, float, float]] = []
    for pos in positions:
        value = _position_value(pos)
        held_score = _num(pos.get("opportunity_score", pos.get("score", 50.0)), 50.0)
        values.append((str(pos.get("symbol", "")).upper(), str(pos.get("sector", "UNKNOWN") or "UNKNOWN").upper(), value, held_score))

    invested = sum(v[2] for v in values)
    largest_value = max((v[2] for v in values), default=0.0)
    sector_values: dict[str, float] = {}
    for _, pos_sector, value, _ in values:
        sector_values[pos_sector] = sector_values.get(pos_sector, 0.0) + value
    largest_sector = max(sector_values.values(), default=0.0)
    current_largest_pct = _clip(largest_value / equity, 0.0, 1.5)
    current_sector_pct = _clip(largest_sector / equity, 0.0, 1.5)

    nonzero_weights = [value / equity for _, _, value, _ in values if value > 0]
    hhi = sum(weight * weight for weight in nonzero_weights)
    diversification = _clip(100.0 * (1.0 - hhi / 0.35), 0.0, 100.0) if nonzero_weights else 100.0
    concentration = _clip(100.0 - current_largest_pct * 170.0 - max(0.0, current_sector_pct - 0.30) * 140.0, 0.0, 100.0)
    liquidity = _clip(
        cash_pct * 120.0 + _clip(excess_liquidity / equity, 0.0, 1.0) * 100.0
        - max(0.0, margin_utilization_pct - 65.0) * 1.5,
        0.0,
        100.0,
    )

    peak = max(equity, _num(portfolio.get("peak_equity"), equity))
    drawdown_pct = max(0.0, (peak - equity) / peak) if peak > 0 else 0.0
    drawdown_resilience = _clip(100.0 - drawdown_pct * 300.0 + cash_pct * 25.0, 0.0, 100.0)
    health = _clip(0.28 * diversification + 0.25 * concentration + 0.22 * liquidity + 0.25 * drawdown_resilience, 0.0, 100.0)

    requested_value = max(0.0, _num(capital.get("recommended_position_value")))
    requested_pct = max(0.0, _num(capital.get("recommended_position_pct")) / 100.0)
    if requested_value <= 0 and requested_pct > 0:
        requested_value = equity * requested_pct

    # Portfolio optimizer caps: preserve liquidity and prevent a single idea or
    # sector from dominating. Existing symbol exposure is included.
    existing_symbol = sum(value for sym, _, value, _ in values if sym == symbol)
    existing_sector = sum(value for _, sec, value, _ in values if sec == sector)
    max_symbol_add = max(0.0, equity * 0.10 - existing_symbol)
    max_sector_add = max(0.0, equity * 0.35 - existing_sector)
    margin_capacity = max(
        0.0,
        equity * leverage_limit * PAPER_MAX_MARGIN_UTILIZATION_PCT - gross_exposure,
    )
    available_capital = min(buying_power, margin_capacity) if PAPER_BROKER_MODE else max(0.0, cash - equity * 0.10)
    recommended_value = min(requested_value, max_symbol_add, max_sector_add, available_capital)

    reserve_cash = equity * 0.05
    cash_spend = min(max(0.0, cash - reserve_cash), recommended_value)
    projected_cash_pct = _clip((cash - cash_spend) / equity, 0.0, 1.0)
    projected_candidate_pct = _clip((existing_symbol + recommended_value) / equity, 0.0, 1.5)
    projected_largest_pct = _clip(max(largest_value, existing_symbol + recommended_value) / equity, 0.0, 1.5)
    projected_candidate_sector_pct = _clip((existing_sector + recommended_value) / equity, 0.0, 1.5)
    projected_sector_pct = _clip(max(largest_sector, existing_sector + recommended_value) / equity, 0.0, 1.5)

    fit = _clip(
        0.27 * quality + 0.18 * probability + 0.14 * min(100.0, rr * 25.0)
        + 0.18 * _num(quant.get("risk_score"), 50.0)
        + 0.13 * _num(quant.get("execution_score"), 50.0)
        + 0.10 * health
        - max(0.0, candidate_corr - 0.70) * 75.0,
        0.0,
        100.0,
    )

    weakest = min(values, key=lambda item: item[3], default=None)
    weakest_symbol = weakest[0] if weakest else None
    weakest_score = weakest[3] if weakest else 0.0
    rotation_candidate = weakest_symbol if weakest_symbol and quality - weakest_score >= 10.0 else None

    actions: list[str] = []
    if PAPER_BROKER_MODE and margin_utilization_pct >= 70.0:
        actions.append("Reduce paper leverage until margin utilization falls below 70%.")
    elif not PAPER_BROKER_MODE and cash_pct < 0.10:
        actions.append("Pause additional buys until cash reserve returns near 10%.")
    if current_largest_pct > 0.10:
        actions.append("Reduce the largest position below 10% of equity.")
    if current_sector_pct > 0.35:
        actions.append("Reduce the most concentrated sector below 35% of equity.")
    if drawdown_pct >= 0.10:
        actions.append("Enter defensive mode and reduce new-trade sizing during drawdown.")
    if rotation_candidate:
        if recommended_value <= 0 or (not PAPER_BROKER_MODE and cash_pct < 0.10):
            actions.append(
                f"{symbol} is the preferred replacement candidate over {rotation_candidate}, "
                "but do not rotate until cash or position capacity is created."
            )
        else:
            actions.append(f"Review rotating {rotation_candidate} into {symbol}; candidate edge is {quality - weakest_score:.1f} points.")
    if not actions:
        actions.append("Portfolio structure is within the V18 operating limits.")

    tail_risk = abs(min(0.0, _num(scenario.get("value_at_risk_95_pct"))))
    hard_veto = (
        recommendation != "BUY"
        or recommended_value < 1.0
        or (not PAPER_BROKER_MODE and projected_cash_pct < 0.055)
        or (PAPER_BROKER_MODE and margin_utilization_pct >= PAPER_MAX_MARGIN_UTILIZATION_PCT * 100.0)
        or projected_candidate_pct > 0.105
        or projected_candidate_sector_pct > 0.355
        or (candidate_corr > 0.88 and current_sector_pct > 0.25)
        or tail_risk > 18.0
        or fit < 58.0
    )
    approved = not hard_veto
    base_multiplier = recommended_value / requested_value if requested_value > 0 else 0.0
    position_multiplier = _clip(base_multiplier * (0.75 + health / 400.0), 0.0, 1.05)
    if not approved:
        position_multiplier = 0.0

    if approved and fit >= 82 and health >= 68:
        verdict = "OPTIMAL ALLOCATION"
    elif approved:
        verdict = "CONTROLLED ALLOCATION"
    elif rotation_candidate and recommended_value <= 0:
        verdict = "ROTATE FIRST"
    else:
        verdict = "WITHHOLD CAPITAL"

    summary = (
        f"{verdict}: portfolio health {health:.1f}/100, candidate fit {fit:.1f}/100, "
        f"target ${recommended_value:,.2f} ({recommended_value / equity:.1%} of equity), "
        f"projected cash reserve {projected_cash_pct:.1%}, "
        f"buying power ${buying_power:,.2f}, leverage {gross_exposure / equity if equity else 0.0:.2f}x."
    )

    return PortfolioSupercomputerAssessment(
        round(health, 2), round(diversification, 2), round(liquidity, 2), round(concentration, 2),
        round(drawdown_resilience, 2), round(fit, 2), round(cash_pct * 100.0, 2), round(projected_cash_pct * 100.0, 2),
        round(current_largest_pct * 100.0, 2), round(projected_largest_pct * 100.0, 2),
        round(current_sector_pct * 100.0, 2), round(projected_sector_pct * 100.0, 2),
        round(recommended_value, 2), round(recommended_value / equity * 100.0, 2), weakest_symbol,
        round(weakest_score, 2), rotation_candidate, tuple(actions), round(position_multiplier, 4),
        approved, hard_veto, verdict, summary,
    )
