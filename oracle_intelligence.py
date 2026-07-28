from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from typing import Any

from quant_trade_standard import QuantTradeAssessment, assess_trade
from market_memory import assess_market_memory, feature_vector, load_trade_memory
from scenario_engine import assess_scenarios
from capital_allocator import assess_capital_allocation
from global_intelligence import assess_global_intelligence
from opportunity_radar import assess_opportunity_radar
from explainable_ai import build_explainability
from research_lab import build_research_report
from portfolio_supercomputer import assess_portfolio_supercomputer
from oracle_one import finalize_decision


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return default if result != result else result
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class OracleDecision:
    symbol: str
    market: str
    action: str
    grade: str
    recommendation: str
    opportunity_score: float
    probability_of_profit: float
    expected_upside_pct: float
    expected_downside_pct: float
    risk_reward_ratio: float
    quant: dict[str, Any]
    memory: dict[str, Any]
    scenario: dict[str, Any]
    capital: dict[str, Any]
    global_intelligence: dict[str, Any]
    radar: dict[str, Any]
    explainability: dict[str, Any]
    research: dict[str, Any]
    portfolio_supercomputer: dict[str, Any]
    oracle_one: dict[str, Any]
    base_opportunity_score: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_opportunity(
    signal: Any,
    *,
    market: str = "cash",
    portfolio_concentration: float = 0.0,
    historical_records: list[dict[str, Any]] | None = None,
    use_market_memory: bool = True,
    min_quality: float = 68.0,
    min_net_ev_pct: float = 0.001,
    max_spread_pct: float = 0.006,
    max_slippage_pct: float = 0.005,
    portfolio: dict[str, Any] | None = None,
    positions: list[dict[str, Any]] | None = None,
    competing_opportunities: list[dict[str, Any]] | None = None,
    global_context: dict[str, Any] | None = None,
) -> OracleDecision:
    assessment: QuantTradeAssessment = assess_trade(
        signal,
        market=market,
        portfolio_concentration=portfolio_concentration,
        min_quality=min_quality,
        min_net_ev_pct=min_net_ev_pct,
        max_spread_pct=max_spread_pct,
        max_slippage_pct=max_slippage_pct,
    )
    symbol = str(_value(signal, "symbol", ""))
    records = historical_records
    if use_market_memory and records is None:
        records = load_trade_memory(market, symbol)
    memory = assess_market_memory(signal, records) if use_market_memory else assess_market_memory(signal, [])
    base_quality = assessment.trade_quality
    global_intelligence = assess_global_intelligence(signal, global_context, market=market)
    radar = assess_opportunity_radar(signal, market=market)
    adjusted_quality = max(0.0, min(100.0, base_quality + memory.score_adjustment + global_intelligence.score_adjustment + radar.radar_adjustment))

    atr_pct = max(0.004, _number(_value(signal, "atr_pct", 0.02), 0.02))
    expected_upside = max(0.012, atr_pct * 2.2)
    expected_downside = max(0.008, atr_pct * 1.25)
    rr = expected_upside / expected_downside if expected_downside else 0.0
    base_probability = max(0.38, min(0.78, 0.42 + assessment.alpha_score / 300 + assessment.relative_value_score / 700))
    probability = max(0.35, min(0.82, base_probability + memory.probability_adjustment_pct / 100.0))
    scenario = assess_scenarios(signal, quant=assessment, memory=memory, market=market)
    probability = max(0.30, min(0.85, 0.55 * probability + 0.45 * scenario.probability_of_profit / 100.0))
    expected_upside = max(expected_upside, scenario.upside_capture_pct / 100.0)
    expected_downside = max(expected_downside, scenario.downside_risk_pct / 100.0)
    rr = expected_upside / expected_downside if expected_downside else 0.0

    if adjusted_quality >= 90:
        grade = "ELITE"
    elif adjusted_quality >= 82:
        grade = "EXCELLENT"
    elif adjusted_quality >= 75:
        grade = "STRONG"
    elif adjusted_quality >= 68:
        grade = "WATCH"
    else:
        grade = "REJECT"

    memory_approved = not memory.veto
    scenario_approved = scenario.approved and not scenario.veto
    global_approved = global_intelligence.approved and not global_intelligence.veto
    radar_approved = radar.approved and not radar.veto

    # V27 paper-mode breathing room. A high-confidence signal with positive
    # expected value may enter as a deliberately small starter position even
    # when its composite quality is slightly below the full-size threshold.
    # This never bypasses an explicit veto, negative EV, poor execution, or
    # portfolio/cash protections applied later in the pipeline.
    paper_breathing_room = os.getenv("PAPER_TRADING_BREATHING_ROOM", "true").lower() == "true"
    raw_confidence = _number(_value(signal, "confidence", 0.0), 0.0)
    confidence_value = raw_confidence if raw_confidence <= 1.0 else raw_confidence / 100.0
    starter_candidate = (
        paper_breathing_room
        and confidence_value >= 0.82
        and adjusted_quality >= 55.0
        and assessment.net_expected_value_pct >= max(0.0025, min_net_ev_pct)
        and assessment.execution_score >= 62.0
        and assessment.risk_score >= 32.0
        and assessment.adverse_selection_score < 70.0
        and memory_approved
        and not scenario.veto
        and not global_intelligence.veto
        and not radar.veto
    )
    full_buy = assessment.approved and memory_approved and scenario_approved and global_approved and radar_approved
    preliminary_recommendation = "BUY" if (full_buy or starter_candidate) else ("WATCH" if adjusted_quality >= 55 and not memory.veto and not radar.veto else "AVOID")
    provisional = {
        "opportunity_score": adjusted_quality,
        "probability_of_profit": probability * 100.0,
        "risk_reward_ratio": rr,
        "recommendation": preliminary_recommendation,
        "quant": assessment.to_dict(),
        "scenario": scenario.to_dict(),
    }
    capital = assess_capital_allocation(
        signal,
        decision=provisional,
        portfolio=portfolio or {"equity": 100.0, "cash": 100.0},
        positions=positions or [],
        competing_opportunities=competing_opportunities or [],
    )
    capital_required = portfolio is not None
    recommendation = preliminary_recommendation
    if capital_required and preliminary_recommendation == "BUY" and not capital.approved:
        recommendation = "WATCH" if capital.rotation_candidate else "AVOID"
    portfolio_supercomputer = assess_portfolio_supercomputer(
        signal,
        decision={
            **provisional,
            "recommendation": recommendation,
            "capital": capital.to_dict(),
        },
        portfolio=portfolio or {"equity": 100.0, "cash": 100.0},
        positions=positions or [],
    )
    if capital_required and recommendation == "BUY" and not portfolio_supercomputer.approved:
        recommendation = "WATCH" if portfolio_supercomputer.rotation_candidate else "AVOID"
    oracle_one = finalize_decision(
        signal,
        recommendation=recommendation,
        opportunity_score=adjusted_quality,
        probability_of_profit=probability * 100.0,
        risk_reward_ratio=rr,
        quant=assessment.to_dict(),
        scenario=scenario.to_dict(),
        capital=capital.to_dict(),
        portfolio_supercomputer=portfolio_supercomputer.to_dict(),
    )
    if recommendation == "BUY" and not oracle_one.approved:
        recommendation = "AVOID"
    action = str(_value(signal, "action", "HOLD")).upper()
    explainability = build_explainability(
        signal,
        quant=assessment,
        memory=memory,
        global_intelligence=global_intelligence,
        radar=radar,
        scenario=scenario,
        capital=capital,
        final_score=adjusted_quality,
        recommendation=recommendation,
    )
    research = build_research_report(
        signal, market=market, quant=assessment, memory=memory,
        global_intelligence=global_intelligence, radar=radar,
        scenario=scenario, capital=capital, explainability=explainability,
    )
    reason = (
        f"{grade}: quality {adjusted_quality:.1f} (base {base_quality:.1f}, memory {memory.score_adjustment:+.1f}), "
        f"net EV {assessment.net_expected_value_pct:.2%}, execution {assessment.execution_score:.0f}, "
        f"risk {assessment.risk_score:.0f}, relative value {assessment.relative_value_score:.0f}, global {global_intelligence.global_score:.0f}, "
        f"radar {radar.setup_score:.0f} ({radar.primary_setup.title()}). "
        f"{memory.summary} {global_intelligence.summary} {radar.summary} {scenario.summary} {capital.summary} "
        f"{portfolio_supercomputer.summary} {oracle_one.summary}"
    )
    return OracleDecision(
        symbol=symbol,
        market=market,
        action=action,
        grade=grade,
        recommendation=recommendation,
        opportunity_score=round(adjusted_quality, 2),
        probability_of_profit=round(probability * 100, 1),
        expected_upside_pct=round(expected_upside * 100, 2),
        expected_downside_pct=round(expected_downside * 100, 2),
        risk_reward_ratio=round(rr, 2),
        quant={**assessment.to_dict(), "approved": bool(assessment.approved and memory_approved and scenario_approved and global_approved and radar_approved)},
        memory=memory.to_dict(),
        scenario=scenario.to_dict(),
        capital=capital.to_dict(),
        global_intelligence=global_intelligence.to_dict(),
        radar=radar.to_dict(),
        explainability=explainability.to_dict(),
        research=research.to_dict(),
        portfolio_supercomputer=portfolio_supercomputer.to_dict(),
        oracle_one=oracle_one.to_dict(),
        base_opportunity_score=round(base_quality, 2),
        reason=reason,
    )
