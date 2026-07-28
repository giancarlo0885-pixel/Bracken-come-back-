from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
        return default if value != value else value
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class ResearchReport:
    symbol: str
    market: str
    thesis: str
    research_rating: str
    research_score: float
    technical_score: float
    fundamental_score: float
    catalyst_score: float
    valuation_score: float
    risk_score: float
    evidence_quality: str
    bull_case: list[str]
    bear_case: list[str]
    catalysts: list[str]
    risks: list[str]
    confirmation_conditions: list[str]
    invalidation_conditions: list[str]
    data_gaps: list[str]
    executive_summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_research_report(
    signal: Any,
    *,
    market: str,
    quant: Any,
    memory: Any,
    global_intelligence: Any,
    radar: Any,
    scenario: Any,
    capital: Any,
    explainability: Any | None = None,
) -> ResearchReport:
    symbol = str(_get(signal, "symbol", "UNKNOWN"))
    trend = _num(_get(signal, "trend_score", _get(signal, "score", 50)), 50)
    momentum = _num(_get(signal, "momentum_score", trend), trend)
    sentiment = _num(_get(signal, "sentiment_score", 50), 50)
    volume = _num(_get(signal, "volume_score", 50), 50)
    confidence = _num(_get(signal, "confidence", 0.5), 0.5)
    if confidence <= 1:
        confidence *= 100

    q_alpha = _num(_get(quant, "alpha_score", 50), 50)
    q_relative = _num(_get(quant, "relative_value_score", 50), 50)
    q_risk = _num(_get(quant, "risk_score", 50), 50)
    global_score = _num(_get(global_intelligence, "global_score", 50), 50)
    radar_score = _num(_get(radar, "setup_score", 50), 50)
    scenario_prob = _num(_get(scenario, "probability_of_profit", 50), 50)
    scenario_ev = _num(_get(scenario, "expected_return_pct", 0), 0)
    memory_win = _num(_get(memory, "analog_win_rate_pct", 50), 50)
    memory_samples = int(_num(_get(memory, "analog_count", 0), 0))
    portfolio_fit = _num(_get(capital, "portfolio_fit_score", 50), 50)

    technical = _clamp(0.30 * trend + 0.25 * momentum + 0.20 * q_alpha + 0.15 * volume + 0.10 * radar_score)
    catalyst = _clamp(0.35 * sentiment + 0.30 * global_score + 0.20 * radar_score + 0.15 * confidence)
    valuation = _clamp(q_relative)
    risk = _clamp(0.45 * q_risk + 0.30 * portfolio_fit + 0.25 * scenario_prob)

    # Fundamentals are deliberately conservative unless actual company fields exist.
    fundamental_fields = ["revenue_growth", "earnings_growth", "profit_margin", "debt_to_equity", "free_cash_flow_growth"]
    present = [k for k in fundamental_fields if _get(signal, k, None) is not None]
    fundamental = 50.0
    data_gaps: list[str] = []
    if present:
        growth = _num(_get(signal, "revenue_growth", 0), 0) + _num(_get(signal, "earnings_growth", 0), 0)
        margin = _num(_get(signal, "profit_margin", 0), 0)
        debt = _num(_get(signal, "debt_to_equity", 1), 1)
        fcf = _num(_get(signal, "free_cash_flow_growth", 0), 0)
        fundamental = _clamp(50 + growth * 35 + margin * 30 + fcf * 20 - max(0, debt - 1) * 10)
    elif market == "cash":
        data_gaps.append("Company fundamentals were not available in the current signal payload; the fundamental score remains neutral.")
    else:
        data_gaps.append("Traditional company fundamentals do not apply to this crypto asset; network and token metrics should be added when available.")

    score = _clamp(0.30 * technical + 0.20 * fundamental + 0.15 * catalyst + 0.15 * valuation + 0.20 * risk)
    if scenario_ev < 0:
        score = _clamp(score - min(8, abs(scenario_ev)))
    if memory_samples >= 5:
        score = _clamp(score + (memory_win - 50) * 0.08)

    if score >= 85:
        rating = "HIGH-CONVICTION RESEARCH"
    elif score >= 75:
        rating = "FAVORABLE RESEARCH"
    elif score >= 65:
        rating = "CONDITIONAL RESEARCH"
    elif score >= 55:
        rating = "NEUTRAL RESEARCH"
    else:
        rating = "UNFAVORABLE RESEARCH"

    primary_setup = str(_get(radar, "primary_setup", "unclassified")).replace("_", " ").title()
    thesis = f"{symbol} is being evaluated as a {primary_setup} setup with a {scenario_prob:.0f}% modeled probability of profit and {scenario_ev:+.2f}% expected scenario return."

    bull_case = [
        f"Technical evidence scores {technical:.0f}/100, supported by trend, momentum, volume, and setup quality.",
        f"The global backdrop scores {global_score:.0f}/100 and the catalyst composite scores {catalyst:.0f}/100.",
        f"Portfolio fit is {portfolio_fit:.0f}/100, indicating how well the opportunity complements current holdings.",
    ]
    if memory_samples >= 3:
        bull_case.append(f"Market Memory found {memory_samples} comparable setups with a {memory_win:.0f}% historical win rate.")

    bear_case = [
        f"The modeled downside and tail-risk remain material even with a {scenario_prob:.0f}% probability of profit.",
        f"Valuation or relative-value evidence is only {valuation:.0f}/100 and may limit upside if competing assets are stronger.",
        "Execution costs, spread expansion, or a change in market regime can eliminate the expected edge before entry.",
    ]

    catalysts = [
        f"Continuation of the {primary_setup.lower()} pattern.",
        "Improving relative strength and volume confirmation.",
        "Supportive macro, sector, or crypto risk-appetite conditions.",
    ]
    risks = [
        "Unexpected news or event risk that changes the original thesis.",
        "A volatility spike, liquidity deterioration, or adverse price gap.",
        "Portfolio concentration or correlation rising after entry.",
    ]

    invalidations = list(_get(explainability, "invalidation_conditions", []) or [])
    if not invalidations:
        invalidations = [
            "Price breaks the modeled stop or support zone.",
            "Net expected value turns negative after updated costs.",
            "The global regime shifts materially against the setup.",
        ]
    confirmations = [
        "Price confirms the planned entry without excessive spread or slippage.",
        "Volume and relative strength remain aligned with the setup.",
        "Scenario probability and expected value remain above the trade threshold.",
    ]

    evidence_count = 6 + len(present) + (1 if memory_samples >= 3 else 0)
    evidence_quality = "HIGH" if evidence_count >= 10 else ("MODERATE" if evidence_count >= 7 else "LIMITED")
    summary = (
        f"{rating}: research score {score:.1f}/100. Technical {technical:.0f}, fundamentals {fundamental:.0f}, "
        f"catalysts {catalyst:.0f}, valuation {valuation:.0f}, and risk quality {risk:.0f}. "
        f"The report is evidence-based and marks unavailable data rather than inventing it."
    )

    return ResearchReport(
        symbol=symbol,
        market=market,
        thesis=thesis,
        research_rating=rating,
        research_score=round(score, 2),
        technical_score=round(technical, 2),
        fundamental_score=round(fundamental, 2),
        catalyst_score=round(catalyst, 2),
        valuation_score=round(valuation, 2),
        risk_score=round(risk, 2),
        evidence_quality=evidence_quality,
        bull_case=bull_case,
        bear_case=bear_case,
        catalysts=catalysts,
        risks=risks,
        confirmation_conditions=confirmations,
        invalidation_conditions=invalidations,
        data_gaps=data_gaps,
        executive_summary=summary,
    )
