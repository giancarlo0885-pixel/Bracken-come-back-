from __future__ import annotations

from dataclasses import dataclass
from typing import Any


KNOWN_SETUPS = (
    "dip_rebound",
    "momentum_breakout",
    "trend_continuation",
    "mean_reversion",
    "mixed_signal",
)


def _value(signal: Any, name: str, default: Any = 0.0) -> Any:
    if isinstance(signal, dict):
        return signal.get(name, default)
    return getattr(signal, name, default)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number and abs(number) != float("inf") else default


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class SetupAttribution:
    tag: str
    confidence: float
    scores: dict[str, float]
    evidence: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "confidence": self.confidence,
            "scores": dict(self.scores),
            "evidence": dict(self.evidence),
        }


def classify_entry_setup(signal: Any) -> SetupAttribution:
    """Classify the technical shape of an entry without changing trade direction.

    This is attribution, not a new execution gate. Council V3 remains responsible
    for BUY/HOLD/SELL. The tag is persisted so paper outcomes can later be grouped
    by the setup that produced them.
    """
    rsi = _finite(_value(signal, "rsi_14", 50.0), 50.0)
    m5 = _finite(_value(signal, "momentum_5d", 0.0))
    m20 = _finite(_value(signal, "momentum_20d", 0.0))
    trend = _finite(_value(signal, "trend_strength", 0.0))
    volume = max(0.0, _finite(_value(signal, "volume_ratio", 1.0), 1.0))

    oversold = _clip((45.0 - rsi) / 20.0)
    deeply_oversold = _clip((35.0 - rsi) / 15.0)
    short_up = _clip(m5 / 0.08)
    short_down = _clip(-m5 / 0.08)
    medium_up = _clip(m20 / 0.20)
    trend_up = _clip(trend / 0.10)
    volume_expansion = _clip((volume - 1.0) / 1.0)
    rsi_trend_zone = _clip(1.0 - abs(rsi - 57.5) / 17.5)

    scores = {
        # Pullback in a constructive medium-term trend followed by short-horizon recovery.
        "dip_rebound": 0.34 * oversold + 0.28 * short_up + 0.24 * medium_up + 0.14 * trend_up,
        # Positive multi-horizon acceleration with participation.
        "momentum_breakout": 0.30 * short_up + 0.28 * medium_up + 0.24 * volume_expansion + 0.18 * trend_up,
        # Persistent trend where momentum is positive but not necessarily explosive.
        "trend_continuation": 0.34 * trend_up + 0.30 * medium_up + 0.20 * rsi_trend_zone + 0.16 * short_up,
        # Oversold move still under pressure; entry thesis is snap-back rather than trend persistence.
        "mean_reversion": 0.48 * deeply_oversold + 0.34 * short_down + 0.18 * (1.0 - medium_up),
    }
    scores = {key: round(_clip(value), 4) for key, value in scores.items()}
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_tag, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0

    # Weak/ambiguous technical shapes should not be mislabeled as a strong pattern.
    if best_score < 0.42:
        tag = "mixed_signal"
        confidence = round(_clip(best_score), 4)
    else:
        tag = best_tag
        confidence = round(_clip(0.55 * best_score + 0.45 * max(0.0, best_score - second_score)), 4)

    return SetupAttribution(
        tag=tag,
        confidence=confidence,
        scores=scores,
        evidence={
            "rsi_14": round(rsi, 4),
            "momentum_5d": round(m5, 6),
            "momentum_20d": round(m20, 6),
            "trend_strength": round(trend, 6),
            "volume_ratio": round(volume, 4),
        },
    )


def attach_setup_attribution(signal: Any) -> SetupAttribution:
    attribution = classify_entry_setup(signal)
    payload = attribution.to_dict()

    if isinstance(signal, dict):
        signal["setup_tag"] = attribution.tag
        signal["setup_confidence"] = attribution.confidence
        signal["setup_scores"] = dict(attribution.scores)
        signal["setup_attribution"] = payload
        base_reason = str(signal.get("reason") or "").strip()
        marker = f"setup={attribution.tag}"
        if marker not in base_reason:
            signal["reason"] = f"{base_reason}; {marker}" if base_reason else marker
    else:
        setattr(signal, "setup_tag", attribution.tag)
        setattr(signal, "setup_confidence", attribution.confidence)
        setattr(signal, "setup_scores", dict(attribution.scores))
        setattr(signal, "setup_attribution", payload)
        base_reason = str(getattr(signal, "reason", "") or "").strip()
        marker = f"setup={attribution.tag}"
        if marker not in base_reason:
            setattr(signal, "reason", f"{base_reason}; {marker}" if base_reason else marker)

    return attribution
