from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from statistics import pstdev
from typing import Any


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _confidence_pct(value: Any) -> float:
    raw = _num(value, 0.0)
    if raw <= 1.0:
        raw *= 100.0
    return _clamp(raw)


@dataclass(frozen=True)
class HybridConfluence:
    score: float
    score_adjustment: float
    cross_signal_agreement: float
    disagreement_index: float
    regime_fit: float
    execution_quality: float
    historical_edge_quality: float
    asymmetry_quality: float
    evidence_completeness: float
    uncertainty_penalty: float
    positive_boost_eligible: bool
    tier: str
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_hybrid_confluence(
    signal: Any,
    *,
    market: str,
    quant: Any,
    memory: Any,
    global_intelligence: Any,
    radar: Any,
    scenario: Any,
) -> HybridConfluence:
    """Build a bounded meta-score from independent Oracle evidence channels.

    The hybrid layer is intentionally incapable of approving a trade on its own.
    It measures agreement, regime compatibility, execution quality, historical
    support, asymmetry, and evidence completeness. Downstream risk/economics
    gates remain authoritative.
    """
    trend = _clamp(_num(_get(signal, "trend_score", _get(signal, "score", 50.0)), 50.0))
    momentum = _clamp(_num(_get(signal, "momentum_score", trend), trend))
    volume = _clamp(_num(_get(signal, "volume_score", 50.0), 50.0))
    sentiment = _clamp(_num(_get(signal, "sentiment_score", 50.0), 50.0))
    confidence = _confidence_pct(_get(signal, "confidence", 0.5))

    alpha = _clamp(_num(_get(quant, "alpha_score", 50.0), 50.0))
    relative_value = _clamp(_num(_get(quant, "relative_value_score", 50.0), 50.0))
    risk_quality = _clamp(_num(_get(quant, "risk_score", 50.0), 50.0))
    execution = _clamp(_num(_get(quant, "execution_score", 50.0), 50.0))
    adverse = _clamp(_num(_get(quant, "adverse_selection_score", 50.0), 50.0))
    net_ev_raw = _num(_get(quant, "net_expected_value_pct", 0.0), 0.0)

    global_score = _clamp(_num(_get(global_intelligence, "global_score", 50.0), 50.0))
    radar_score = _clamp(_num(_get(radar, "setup_score", 50.0), 50.0))
    scenario_probability = _clamp(_num(_get(scenario, "probability_of_profit", 50.0), 50.0))
    scenario_ev = _num(_get(scenario, "expected_return_pct", 0.0), 0.0)

    memory_samples = max(0, int(_num(_get(memory, "analog_count", 0), 0.0)))
    memory_win_rate = _clamp(_num(_get(memory, "analog_win_rate_pct", 50.0), 50.0))
    sample_confidence = min(1.0, memory_samples / 20.0)
    historical_edge = _clamp(50.0 + (memory_win_rate - 50.0) * sample_confidence)

    technical = _clamp(
        0.26 * trend
        + 0.22 * momentum
        + 0.16 * volume
        + 0.20 * alpha
        + 0.16 * radar_score
    )
    catalyst = _clamp(0.45 * sentiment + 0.35 * global_score + 0.20 * confidence)
    scenario_strength = _clamp(0.60 * scenario_probability + 0.40 * radar_score)

    agreement_components = [
        technical,
        catalyst,
        relative_value,
        risk_quality,
        scenario_strength,
        historical_edge,
    ]
    dispersion = pstdev(agreement_components) if len(agreement_components) > 1 else 0.0
    cross_signal_agreement = _clamp(100.0 - dispersion * 2.25)
    disagreement_index = _clamp(100.0 - cross_signal_agreement)

    regime_fit = _clamp(
        0.34 * global_score
        + 0.31 * radar_score
        + 0.25 * scenario_probability
        + 0.10 * risk_quality
    )

    execution_quality = _clamp(
        0.62 * execution
        + 0.23 * (100.0 - adverse)
        + 0.15 * confidence
    )

    # Convert small fractional EV values and percentage-point EV values into a
    # bounded directional quality score without pretending they are calibrated.
    if abs(net_ev_raw) <= 1.0:
        net_ev_points = net_ev_raw * 100.0
    else:
        net_ev_points = net_ev_raw
    asymmetry_quality = _clamp(
        50.0
        + max(-25.0, min(25.0, scenario_ev * 3.0))
        + max(-20.0, min(20.0, net_ev_points * 4.0))
    )

    expected_fields = [
        "trend_score",
        "momentum_score",
        "volume_score",
        "sentiment_score",
        "confidence",
        "atr_pct",
        "spread_pct",
    ]
    if str(market or "").lower() == "cash":
        expected_fields += [
            "revenue_growth",
            "earnings_growth",
            "profit_margin",
            "debt_to_equity",
            "free_cash_flow_growth",
        ]
    else:
        expected_fields += [
            "relative_strength",
            "volatility_20d",
            "distance_from_vwap_pct",
        ]
    present = sum(1 for key in expected_fields if _get(signal, key, None) is not None)
    evidence_completeness = _clamp(100.0 * present / max(1, len(expected_fields)))

    uncertainty_penalty = _clamp(
        0.44 * disagreement_index
        + 0.34 * (100.0 - evidence_completeness)
        + 0.22 * (100.0 - min(100.0, memory_samples * 5.0))
    )

    hybrid_score = _clamp(
        0.24 * technical
        + 0.15 * catalyst
        + 0.13 * regime_fit
        + 0.15 * execution_quality
        + 0.12 * historical_edge
        + 0.11 * asymmetry_quality
        + 0.10 * evidence_completeness
        - 0.12 * uncertainty_penalty
    )

    explicit_veto = bool(
        _get(memory, "veto", False)
        or _get(global_intelligence, "veto", False)
        or _get(radar, "veto", False)
        or _get(scenario, "veto", False)
    )
    positive_boost_eligible = bool(
        not explicit_veto
        and scenario_ev > 0.0
        and net_ev_raw > 0.0
        and execution_quality >= 65.0
        and cross_signal_agreement >= 58.0
        and uncertainty_penalty <= 45.0
        and evidence_completeness >= 50.0
    )

    raw_adjustment = (hybrid_score - 60.0) * 0.10
    if raw_adjustment > 0.0 and not positive_boost_eligible:
        raw_adjustment = 0.0
    score_adjustment = max(-5.0, min(4.0, raw_adjustment))

    if hybrid_score >= 82.0 and positive_boost_eligible:
        tier = "SUPER_HYBRID"
    elif hybrid_score >= 72.0:
        tier = "HIGH_CONFLUENCE"
    elif hybrid_score >= 60.0:
        tier = "MIXED_CONFLUENCE"
    else:
        tier = "DEFENSIVE"

    summary = (
        f"{tier}: hybrid {hybrid_score:.1f}/100; agreement {cross_signal_agreement:.1f}, "
        f"regime fit {regime_fit:.1f}, execution {execution_quality:.1f}, "
        f"historical edge {historical_edge:.1f}, asymmetry {asymmetry_quality:.1f}, "
        f"evidence {evidence_completeness:.1f}, uncertainty {uncertainty_penalty:.1f}; "
        f"bounded score adjustment {score_adjustment:+.2f}."
    )

    return HybridConfluence(
        score=round(hybrid_score, 2),
        score_adjustment=round(score_adjustment, 2),
        cross_signal_agreement=round(cross_signal_agreement, 2),
        disagreement_index=round(disagreement_index, 2),
        regime_fit=round(regime_fit, 2),
        execution_quality=round(execution_quality, 2),
        historical_edge_quality=round(historical_edge, 2),
        asymmetry_quality=round(asymmetry_quality, 2),
        evidence_completeness=round(evidence_completeness, 2),
        uncertainty_penalty=round(uncertainty_penalty, 2),
        positive_boost_eligible=positive_boost_eligible,
        tier=tier,
        summary=summary,
    )


__all__ = ["HybridConfluence", "assess_hybrid_confluence"]
