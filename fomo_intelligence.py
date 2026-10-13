"""FOMO context for Oracle Opportunity Radar.

Read-only research heuristics. These scores are NOT probabilities, forecasts, or
permission to change orders, model promotion, sizing, or execution policy.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any


_VERSION = "fomo-shadow-v1"


def _get(signal: Any, name: str) -> Any:
    return signal.get(name) if isinstance(signal, dict) else getattr(signal, name, None)


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _unit(value: float) -> float:
    return max(0.0, min(1.0, value))


@dataclass(frozen=True)
class FomoAssessment:
    version: str
    phase: str
    available: bool
    intensity_score: float | None
    exhaustion_score: float | None
    reason: str
    observed_features: dict[str, float]
    missing_features: list[str]
    research_only: bool = True
    calibrated_probability: bool = False
    execution_impact: str = "NONE"
    promotion_authorized: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_fomo(signal: Any) -> FomoAssessment:
    """Classify emerging, accelerating and exhausted momentum at observation time.

    Volume ratio is a *relative participation* proxy, not a true derivative of
    volume; RSI and Bollinger position are extension proxies, not sentiment.
    Inputs are existing indicator snapshots. This never modifies the signal.
    Missing/non-finite fields fail closed as unavailable rather than producing
    a neutral or bullish reading from made-up values.
    """
    required = (
        "momentum_5d", "momentum_20d", "volume_ratio",
        "trend_strength", "rsi_14", "bollinger_position",
    )
    features = {name: _finite(_get(signal, name)) for name in required}
    missing = [name for name, value in features.items() if value is None]
    rsi, volume = features["rsi_14"], features["volume_ratio"]
    if rsi is not None and not 0.0 <= rsi <= 100.0:
        missing.append("rsi_14:out_of_range")
    if volume is not None and volume <= 0:
        missing.append("volume_ratio:nonpositive")
    if missing:
        return FomoAssessment(
            version=_VERSION,
            phase="unavailable",
            available=False,
            intensity_score=None,
            exhaustion_score=None,
            reason="Missing or invalid point-in-time indicators; no FOMO classification.",
            observed_features={k: v for k, v in features.items() if v is not None},
            missing_features=missing,
        )

    m5 = features["momentum_5d"]
    m20 = features["momentum_20d"]
    trend = features["trend_strength"]
    rsi = features["rsi_14"]
    volume = features["volume_ratio"]
    band = features["bollinger_position"]

    # Uncalibrated [0, 100] heuristic scores; NOT probabilities or expected EV.
    intensity = 100.0 * (
        0.45 * _unit(m5 / 0.06)
        + 0.25 * _unit(m20 / 0.15)
        + 0.30 * _unit((volume - 1.0) / 2.0)
    )
    exhaustion = 100.0 * (
        0.50 * _unit((rsi - 70.0) / 18.0)
        + 0.35 * _unit((band - 0.90) / 0.30)
        + 0.15 * _unit((volume - 3.0) / 2.0)
    )

    if (
        m20 > 0
        and volume >= 1.5
        and (rsi >= 82.0 or band >= 1.12 or (m5 <= 0 and volume >= 1.8 and m20 >= 0.03))
    ):
        phase = "exhaustion"
        reason = "Elevated participation alongside extension or fading recent momentum."
    elif m5 >= 0.04 and m20 >= 0.08 and trend > 0 and volume >= 1.8 and rsi < 82 and band < 1.12:
        phase = "acceleration"
        reason = "Strong positive near-term trend with unusually high relative participation."
    elif m5 >= 0.015 and m20 >= 0.03 and trend > 0 and volume >= 1.25 and rsi < 78 and band < 1.0:
        phase = "emerging"
        reason = "Positive momentum and relative participation without extreme extension."
    else:
        phase = "quiet"
        reason = "No qualifying FOMO phase from the available indicator snapshot."

    return FomoAssessment(
        version=_VERSION,
        phase=phase,
        available=True,
        intensity_score=round(intensity, 2),
        exhaustion_score=round(exhaustion, 2),
        reason=reason,
        observed_features={k: float(v) for k, v in features.items()},
        missing_features=[],
    )
