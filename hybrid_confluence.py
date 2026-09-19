from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from statistics import pstdev
from typing import Any

from config import (
    SUPER_HYBRID_MAX_NEGATIVE_ADJUSTMENT,
    SUPER_HYBRID_MAX_POSITIVE_ADJUSTMENT,
    SUPER_HYBRID_MAX_UNCERTAINTY,
    SUPER_HYBRID_MIN_AGREEMENT,
    SUPER_HYBRID_MIN_ADVERSARIAL_MARGIN,
    SUPER_HYBRID_MIN_COST_ADJUSTED_CONVICTION,
    SUPER_HYBRID_MIN_EVIDENCE_COMPLETENESS,
    SUPER_HYBRID_MIN_EXECUTION_QUALITY,
    SUPER_HYBRID_MIN_SOURCE_DIVERSITY,
)


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


def _fraction(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    parsed = _num(value, float("nan"))
    if not math.isfinite(parsed):
        return default
    if abs(parsed) > 1.0:
        parsed /= 100.0
    return max(0.0, min(1.0, parsed))


def _observed_cost_pct(signal: Any, quant: Any) -> float:
    explicit = _get(quant, "estimated_cost_pct", None)
    if explicit is not None:
        return max(0.0, _num(explicit, 0.0))
    spread = max(0.0, _num(_get(signal, "spread_pct", 0.0), 0.0))
    slippage = max(
        0.0,
        _num(
            _get(signal, "estimated_slippage_pct", _get(signal, "slippage_pct", 0.0)),
            0.0,
        ),
    )
    fees = max(
        0.0,
        _num(
            _get(signal, "estimated_fees_pct", _get(signal, "fee_pct", 0.0)),
            0.0,
        ),
    )
    impact = max(0.0, _num(_get(signal, "estimated_market_impact_pct", 0.0), 0.0))
    return spread + slippage + fees + impact


def _source_diversity_score(
    signal: Any,
    *,
    quant: Any,
    memory: Any,
    global_intelligence: Any,
    scenario: Any,
) -> float:
    technical = any(
        _get(signal, key, None) is not None
        for key in ("trend_score", "trend_strength", "momentum_score", "momentum_20d", "volume_score", "volume_ratio")
    )
    sentiment_macro = any(
        _get(signal, key, None) is not None
        for key in ("sentiment_score", "news_sentiment", "event_risk_score")
    ) or _get(global_intelligence, "global_score", None) is not None
    relative_value = _get(quant, "relative_value_score", None) is not None
    historical = max(0, int(_num(_get(memory, "analog_count", 0), 0.0))) >= 3
    scenario_evidence = _get(scenario, "probability_of_profit", None) is not None
    execution = (
        _get(quant, "execution_score", None) is not None
        and _get(quant, "adverse_selection_score", None) is not None
    )
    regime = any(
        _get(signal, key, None) not in (None, "")
        for key in ("regime", "market_regime")
    )
    families = (
        technical,
        sentiment_macro,
        relative_value,
        historical,
        scenario_evidence,
        execution,
        regime,
    )
    return _clamp(100.0 * sum(1 for present in families if present) / len(families))


def _regime_memory_score(signal: Any, memory: Any) -> float:
    current_regime = str(
        _get(signal, "market_regime", _get(signal, "regime", "")) or ""
    ).strip().lower()
    analogs = list(_get(memory, "analogs", []) or [])
    if not current_regime or not analogs:
        return 50.0
    matches = [
        item for item in analogs
        if str(_get(item, "regime", "") or "").strip().lower() == current_regime
    ]
    if len(matches) < 3:
        return 50.0
    returns = [_num(_get(item, "return_pct", 0.0), 0.0) for item in matches]
    win_rate = sum(1 for value in returns if value > 0.0) / len(returns)
    avg_return = sum(returns) / len(returns)
    return _clamp(
        50.0
        + (win_rate - 0.5) * 60.0
        + max(-20.0, min(20.0, avg_return * 400.0))
    )


def _edge_persistence_score(signal: Any) -> float:
    observed = None
    for key in (
        "consecutive_confirmations",
        "confirmation_count",
        "persistence_scans",
        "signal_confirmations",
    ):
        value = _get(signal, key, None)
        if value is not None:
            observed = max(0, int(_num(value, 0.0)))
            break
    if observed is None:
        return 50.0
    if observed <= 0:
        return 20.0
    if observed == 1:
        return 40.0
    if observed == 2:
        return 60.0
    if observed == 3:
        return 75.0
    return min(100.0, 85.0 + 3.0 * min(5, observed - 4))


def _calibration_confidence_score(signal: Any) -> float:
    sample_count = max(
        0,
        int(
            _num(
                _get(
                    signal,
                    "forecast_validation_samples",
                    _get(signal, "validation_sample_count", 0),
                ),
                0.0,
            )
        ),
    )
    accuracy = _fraction(
        _get(
            signal,
            "directional_accuracy",
            _get(signal, "forecast_directional_accuracy", None),
        ),
        None,
    )
    calibration_error = _fraction(
        _get(
            signal,
            "calibration_error",
            _get(signal, "forecast_calibration_error", None),
        ),
        None,
    )
    if sample_count <= 0 and accuracy is None and calibration_error is None:
        return 50.0
    sample_quality = min(100.0, sample_count * 2.0)
    accuracy_quality = 50.0 if accuracy is None else _clamp(50.0 + (accuracy - 0.5) * 160.0)
    calibration_quality = (
        50.0
        if calibration_error is None
        else _clamp(100.0 - calibration_error * 400.0)
    )
    return _clamp(
        0.35 * sample_quality
        + 0.40 * accuracy_quality
        + 0.25 * calibration_quality
    )


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
    source_diversity: float
    cost_adjusted_conviction: float
    regime_memory_quality: float
    adversarial_margin: float
    edge_persistence: float
    calibration_confidence: float
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
    raw_memory_win_rate = _get(memory, "analog_win_rate_pct", None)
    if raw_memory_win_rate is None:
        raw_memory_win_rate = _get(memory, "win_rate", 0.5)
    memory_win_rate = _confidence_pct(raw_memory_win_rate)
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
    source_diversity = _source_diversity_score(
        signal,
        quant=quant,
        memory=memory,
        global_intelligence=global_intelligence,
        scenario=scenario,
    )

    observed_cost = _observed_cost_pct(signal, quant)
    if net_ev_raw <= 0.0:
        cost_adjusted_conviction = 0.0
    else:
        gross_edge = net_ev_raw + observed_cost
        cost_adjusted_conviction = _clamp(
            100.0 * net_ev_raw / max(gross_edge, 1e-12)
        )

    regime_memory_quality = _regime_memory_score(signal, memory)
    edge_persistence = _edge_persistence_score(signal)
    calibration_confidence = _calibration_confidence_score(signal)

    uncertainty_penalty = _clamp(
        0.40 * disagreement_index
        + 0.28 * (100.0 - evidence_completeness)
        + 0.18 * (100.0 - min(100.0, memory_samples * 5.0))
        + 0.14 * (100.0 - calibration_confidence)
    )

    bull_strength = _clamp(
        0.24 * technical
        + 0.18 * catalyst
        + 0.18 * scenario_strength
        + 0.15 * historical_edge
        + 0.15 * asymmetry_quality
        + 0.10 * regime_memory_quality
    )
    bear_pressure = _clamp(
        0.30 * adverse
        + 0.22 * (100.0 - risk_quality)
        + 0.18 * uncertainty_penalty
        + 0.15 * disagreement_index
        + 0.15 * (100.0 - cost_adjusted_conviction)
    )
    adversarial_margin = max(-100.0, min(100.0, bull_strength - bear_pressure))

    hybrid_score = _clamp(
        0.18 * technical
        + 0.11 * catalyst
        + 0.11 * regime_fit
        + 0.13 * execution_quality
        + 0.09 * historical_edge
        + 0.10 * asymmetry_quality
        + 0.07 * evidence_completeness
        + 0.07 * source_diversity
        + 0.06 * cost_adjusted_conviction
        + 0.04 * regime_memory_quality
        + 0.02 * edge_persistence
        + 0.02 * calibration_confidence
        - 0.10 * uncertainty_penalty
        - 0.08 * max(0.0, -adversarial_margin)
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
        and execution_quality >= SUPER_HYBRID_MIN_EXECUTION_QUALITY
        and cross_signal_agreement >= SUPER_HYBRID_MIN_AGREEMENT
        and uncertainty_penalty <= SUPER_HYBRID_MAX_UNCERTAINTY
        and evidence_completeness >= SUPER_HYBRID_MIN_EVIDENCE_COMPLETENESS
        and source_diversity >= SUPER_HYBRID_MIN_SOURCE_DIVERSITY
        and cost_adjusted_conviction >= SUPER_HYBRID_MIN_COST_ADJUSTED_CONVICTION
        and adversarial_margin >= SUPER_HYBRID_MIN_ADVERSARIAL_MARGIN
    )

    raw_adjustment = (hybrid_score - 60.0) * 0.10
    if raw_adjustment > 0.0 and not positive_boost_eligible:
        raw_adjustment = 0.0
    score_adjustment = max(
        -SUPER_HYBRID_MAX_NEGATIVE_ADJUSTMENT,
        min(SUPER_HYBRID_MAX_POSITIVE_ADJUSTMENT, raw_adjustment),
    )

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
        f"evidence {evidence_completeness:.1f}, diversity {source_diversity:.1f}, "
        f"cost conviction {cost_adjusted_conviction:.1f}, regime memory {regime_memory_quality:.1f}, "
        f"adversarial margin {adversarial_margin:+.1f}, persistence {edge_persistence:.1f}, "
        f"calibration {calibration_confidence:.1f}, uncertainty {uncertainty_penalty:.1f}; "
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
        source_diversity=round(source_diversity, 2),
        cost_adjusted_conviction=round(cost_adjusted_conviction, 2),
        regime_memory_quality=round(regime_memory_quality, 2),
        adversarial_margin=round(adversarial_margin, 2),
        edge_persistence=round(edge_persistence, 2),
        calibration_confidence=round(calibration_confidence, 2),
        uncertainty_penalty=round(uncertainty_penalty, 2),
        positive_boost_eligible=positive_boost_eligible,
        tier=tier,
        summary=summary,
    )


__all__ = ["HybridConfluence", "assess_hybrid_confluence"]
