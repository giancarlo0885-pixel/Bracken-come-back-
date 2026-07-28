from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


def _value(obj: Any, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return default if result != result else result
    except (TypeError, ValueError):
        return default


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class EngineVote:
    engine: str
    verdict: str
    score: float
    weight: float
    contribution: float
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExplainabilityReport:
    consensus_score: float
    consensus_label: str
    agreement_pct: float
    confidence_quality: str
    engine_votes: list[dict[str, Any]]
    strongest_drivers: list[str]
    conflicts: list[str]
    invalidation_conditions: list[str]
    sizing_explanation: list[str]
    decision_path: list[str]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _vote(engine: str, score: float, weight: float, approved: bool, veto: bool, evidence: str) -> EngineVote:
    verdict = "VETO" if veto else ("SUPPORT" if approved and score >= 55 else "CAUTION" if score >= 42 else "OPPOSE")
    signed = 1.0 if verdict == "SUPPORT" else 0.25 if verdict == "CAUTION" else -1.0 if verdict in {"VETO", "OPPOSE"} else 0.0
    return EngineVote(engine, verdict, round(score, 2), weight, round(weight * signed, 3), evidence)


def build_explainability(
    signal: Any,
    *,
    quant: Any,
    memory: Any,
    global_intelligence: Any,
    radar: Any,
    scenario: Any,
    capital: Any,
    final_score: float,
    recommendation: str,
) -> ExplainabilityReport:
    """Build a transparent evidence ledger from every Oracle engine.

    This module does not change the trading decision. It explains the exact
    decision already produced by the deterministic engines, including vetoes,
    conflicts, invalidation rules and position-size reasoning.
    """
    quant_score = _number(_value(quant, "trade_quality"), final_score)
    memory_score = _clip(50 + _number(_value(memory, "score_adjustment")) * 6)
    global_score = _number(_value(global_intelligence, "global_score"), 50)
    radar_score = _number(_value(radar, "setup_score"), 50)
    scenario_score = _clip(
        0.55 * _number(_value(scenario, "probability_of_profit"), 50)
        + 0.45 * _clip(50 + _number(_value(scenario, "expected_return_pct")) * 4)
    )
    capital_score = _number(_value(capital, "capital_priority_score"), 50)

    votes = [
        _vote("Quant Standard", quant_score, 0.30, bool(_value(quant, "approved", False)), False, str(_value(quant, "reason", "Core alpha, execution, risk and expected-value test."))),
        _vote("Market Memory", memory_score, 0.12, not bool(_value(memory, "veto", False)), bool(_value(memory, "veto", False)), str(_value(memory, "summary", "Historical analog evidence is still building."))),
        _vote("Global Intelligence", global_score, 0.14, bool(_value(global_intelligence, "approved", True)), bool(_value(global_intelligence, "veto", False)), str(_value(global_intelligence, "summary", "Cross-market conditions are mixed."))),
        _vote("Opportunity Radar", radar_score, 0.14, bool(_value(radar, "approved", False)), bool(_value(radar, "veto", False)), str(_value(radar, "summary", "Setup classification is still building."))),
        _vote("Scenario Engine", scenario_score, 0.16, bool(_value(scenario, "approved", False)), bool(_value(scenario, "veto", False)), str(_value(scenario, "summary", "Scenario distribution is still building."))),
        _vote("Capital Allocator", capital_score, 0.14, bool(_value(capital, "approved", True)), bool(_value(capital, "veto", False)), str(_value(capital, "summary", "Portfolio-fit analysis is still building."))),
    ]
    support_weight = sum(v.weight for v in votes if v.verdict == "SUPPORT")
    oppose_weight = sum(v.weight for v in votes if v.verdict in {"OPPOSE", "VETO"})
    agreement = _clip(100 * max(support_weight, oppose_weight))
    weighted_score = sum(v.score * v.weight for v in votes) / max(sum(v.weight for v in votes), 0.001)
    consensus_score = _clip(0.65 * weighted_score + 0.35 * final_score)

    if any(v.verdict == "VETO" for v in votes):
        consensus_label = "VETO CONFLICT"
    elif recommendation == "BUY" and agreement >= 72:
        consensus_label = "HIGH CONSENSUS"
    elif recommendation == "BUY":
        consensus_label = "QUALIFIED CONSENSUS"
    elif recommendation == "WATCH":
        consensus_label = "MIXED EVIDENCE"
    else:
        consensus_label = "REJECTED"

    sample = _number(_value(memory, "effective_sample_size"), _number(_value(memory, "sample_size")))
    uncertainty = _number(_value(scenario, "uncertainty_pct"), 20)
    if sample >= 20 and uncertainty <= 12 and agreement >= 75:
        confidence_quality = "ROBUST"
    elif sample >= 8 or uncertainty <= 20:
        confidence_quality = "MODERATE"
    else:
        confidence_quality = "EARLY EVIDENCE"

    drivers: list[str] = []
    conflicts: list[str] = []
    for vote in votes:
        line = f"{vote.engine}: {vote.evidence}"
        if vote.verdict == "SUPPORT":
            drivers.append(line)
        elif vote.verdict in {"OPPOSE", "VETO"}:
            conflicts.append(line)
    drivers = drivers[:5]
    conflicts = conflicts[:5]

    price = _number(_value(signal, "price"), _number(_value(signal, "current_price")))
    atr_pct = max(0.005, _number(_value(signal, "atr_pct"), 0.02))
    invalidation = [
        f"Price closes roughly {atr_pct * 1.25:.1%} below the modeled entry zone" if price else f"Price violates the stop zone by roughly {atr_pct * 1.25:.1%}",
        "Net expected value falls to zero or below after spread and slippage",
        "The market regime changes against the trade and global confirmation turns negative",
        "The setup becomes overcrowded or the Radar issues a late-entry veto",
        "Portfolio concentration, correlation, or cash-reserve limits are breached",
    ]
    if _number(_value(signal, "event_risk")) > 0.5:
        invalidation.insert(1, "A scheduled event materially changes the original thesis")

    sizing = [
        f"Quant multiplier: {_number(_value(quant, 'position_multiplier'), 1.0):.2f}×",
        f"Global multiplier: {_number(_value(global_intelligence, 'position_multiplier'), 1.0):.2f}×",
        f"Radar multiplier: {_number(_value(radar, 'position_multiplier'), 1.0):.2f}×",
        f"Scenario multiplier: {_number(_value(scenario, 'position_multiplier'), 1.0):.2f}×",
        f"Capital allocator multiplier: {_number(_value(capital, 'final_multiplier'), 1.0):.2f}×",
    ]
    combined = 1.0
    for value in [
        _number(_value(quant, "position_multiplier"), 1.0),
        _number(_value(global_intelligence, "position_multiplier"), 1.0),
        _number(_value(radar, "position_multiplier"), 1.0),
        _number(_value(scenario, "position_multiplier"), 1.0),
        _number(_value(capital, "final_multiplier"), 1.0),
    ]:
        combined *= value
    sizing.append(f"Combined pre-cap sizing influence: {combined:.2f}×")

    path = [
        f"Quant gate produced {quant_score:.1f}/100",
        f"Market Memory adjusted the evidence by {_number(_value(memory, 'score_adjustment')):+.1f} points",
        f"Global Intelligence contributed {_number(_value(global_intelligence, 'score_adjustment')):+.1f} points",
        f"Opportunity Radar contributed {_number(_value(radar, 'radar_adjustment')):+.1f} points",
        f"Scenario Engine estimated {_number(_value(scenario, 'probability_of_profit')):.1f}% profitable paths",
        f"Capital Allocator returned {str(_value(capital, 'verdict', 'BUILDING'))}",
        f"Final result: {recommendation} at {final_score:.1f}/100",
    ]

    summary = (
        f"{consensus_label}: {agreement:.0f}% weighted engine agreement, "
        f"consensus quality {consensus_score:.1f}/100, and {confidence_quality.lower()} confidence quality. "
        f"The final recommendation is {recommendation}."
    )
    return ExplainabilityReport(
        consensus_score=round(consensus_score, 2),
        consensus_label=consensus_label,
        agreement_pct=round(agreement, 1),
        confidence_quality=confidence_quality,
        engine_votes=[v.to_dict() for v in votes],
        strongest_drivers=drivers,
        conflicts=conflicts,
        invalidation_conditions=invalidation,
        sizing_explanation=sizing,
        decision_path=path,
        summary=summary,
    )
