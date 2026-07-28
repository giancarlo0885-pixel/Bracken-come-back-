from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


def _value(obj: Any, name: str, default: Any = 0.0) -> Any:
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
class RadarAssessment:
    primary_setup: str
    secondary_setup: str
    setup_score: float
    urgency_score: float
    durability_score: float
    catalyst_score: float
    crowding_risk: float
    radar_adjustment: float
    position_multiplier: float
    approved: bool
    veto: bool
    reasons: list[str]
    warnings: list[str]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_opportunity_radar(signal: Any, *, market: str = "cash") -> RadarAssessment:
    """Classify the setup and measure whether it is actionable now.

    The radar is intentionally strategy-agnostic. It does not manufacture a BUY;
    it identifies the best-fitting setup and penalizes stale, crowded, or
    internally conflicting opportunities.
    """
    m5 = _number(_value(signal, "momentum_5d"))
    m20 = _number(_value(signal, "momentum_20d"))
    trend = _number(_value(signal, "trend_strength"))
    rsi = _number(_value(signal, "rsi_14", 50.0), 50.0)
    volume = max(0.0, _number(_value(signal, "volume_ratio", 1.0), 1.0))
    sentiment = _number(_value(signal, "news_sentiment"))
    macd_hist = _number(_value(signal, "macd_hist"))
    atr_pct = max(0.001, _number(_value(signal, "atr_pct", 0.02), 0.02))
    bollinger = _number(_value(signal, "bollinger_position", 0.5), 0.5)
    volatility = max(0.0, _number(_value(signal, "volatility_20d", 0.25), 0.25))
    regime = str(_value(signal, "regime", "mixed")).lower()

    # Independent strategy lenses. Scores are comparable but not probabilities.
    breakout = _clip(
        45
        + 220 * max(m5, 0)
        + 120 * max(m20, 0)
        + 90 * max(trend, 0)
        + 12 * max(volume - 1.0, 0)
        + 8 * max(sentiment, 0)
        - 16 * max(bollinger - 1.05, 0)
    )
    trend_continuation = _clip(
        45
        + 150 * max(m20, 0)
        + 130 * max(trend, 0)
        + 80 * max(m5, 0)
        + 6 * max(volume - 0.9, 0)
        - 0.30 * max(rsi - 78, 0)
    )
    mean_reversion = _clip(
        35
        + 1.4 * max(45 - rsi, 0)
        + 55 * max(0.25 - bollinger, 0)
        + 45 * max(-m5, 0)
        + 20 * max(sentiment, 0)
        - 90 * max(-trend, 0.08)
    )
    sector_leadership = _clip(
        42
        + 130 * max(m20, 0)
        + 100 * max(trend, 0)
        + 10 * max(volume - 1.0, 0)
        + (8 if regime in {"risk-on", "bull", "bullish"} else 0)
    )
    event_driven = _clip(
        35
        + 28 * abs(sentiment)
        + 10 * max(volume - 1.0, 0)
        + 80 * abs(m5)
        + 15 * min(abs(macd_hist) / max(abs(_number(_value(signal, "price", 1.0), 1.0)) * 0.01, 1e-6), 1.0)
    )
    defensive_rotation = _clip(
        38
        + 110 * max(trend, 0)
        + 80 * max(m20, 0)
        + (18 if regime in {"risk-off", "bear", "bearish"} else 0)
        - 35 * max(volatility - 0.45, 0)
    )
    crypto_risk_on = _clip(
        38
        + (12 if market == "crypto" else -12)
        + 170 * max(m20, 0)
        + 120 * max(m5, 0)
        + 9 * max(volume - 1.0, 0)
        + (12 if regime in {"risk-on", "bull", "bullish"} else 0)
        - 25 * max(volatility - 0.90, 0)
    )

    scores = {
        "MOMENTUM BREAKOUT": breakout,
        "TREND CONTINUATION": trend_continuation,
        "MEAN REVERSION": mean_reversion,
        "SECTOR LEADERSHIP": sector_leadership,
        "EVENT DRIVEN": event_driven,
        "DEFENSIVE ROTATION": defensive_rotation,
        "CRYPTO RISK-ON": crypto_risk_on,
    }
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    primary, setup_score = ordered[0]
    secondary, secondary_score = ordered[1]

    urgency = _clip(
        40
        + 140 * abs(m5)
        + 10 * max(volume - 1.0, 0)
        + 10 * abs(sentiment)
        + 8 * min(atr_pct / 0.03, 2.0)
    )
    durability = _clip(
        45
        + 130 * max(m20, 0)
        + 110 * max(trend, 0)
        + 8 * max(volume - 0.8, 0)
        - 24 * max(volatility - 0.55, 0)
        - 0.45 * max(rsi - 80, 0)
    )
    catalyst = _clip(42 + 26 * abs(sentiment) + 8 * max(volume - 1.0, 0) + 70 * abs(m5))
    crowding = _clip(
        10
        + 1.4 * max(rsi - 68, 0)
        + 65 * max(bollinger - 0.88, 0)
        + 25 * max(volume - 2.4, 0)
        + 20 * max(volatility - 0.85, 0)
    )

    setup_separation = setup_score - secondary_score
    radar_adjustment = _clip(
        (setup_score - 65) * 0.08
        + (durability - 55) * 0.035
        + (urgency - 55) * 0.02
        - crowding * 0.035,
        -6.0,
        6.0,
    )
    multiplier = _clip(0.75 + (setup_score - 60) / 100 + (durability - 50) / 200 - crowding / 250, 0.35, 1.20)

    reasons: list[str] = []
    warnings: list[str] = []
    if setup_score >= 75:
        reasons.append(f"{primary.title()} setup is strongly expressed")
    if durability >= 65:
        reasons.append("multi-session trend durability is supportive")
    if urgency >= 70:
        reasons.append("price and volume conditions are actionable now")
    if catalyst >= 65:
        reasons.append("catalyst intensity is above normal")
    if setup_separation < 5:
        warnings.append("setup classification is mixed")
    if crowding >= 65:
        warnings.append("crowding and late-entry risk are elevated")
    if rsi >= 82 and primary in {"MOMENTUM BREAKOUT", "TREND CONTINUATION", "CRYPTO RISK-ON"}:
        warnings.append("momentum is extremely extended")
    if volatility >= (1.15 if market == "crypto" else 0.75):
        warnings.append("realized volatility is unusually high")

    veto = bool(crowding >= 84 or (setup_score < 48 and durability < 45) or (len(warnings) >= 3 and setup_score < 65))
    approved = bool(not veto and setup_score >= 58 and durability >= 42)
    summary = (
        f"Radar classifies this as {primary.title()} ({setup_score:.0f}/100), "
        f"with urgency {urgency:.0f}, durability {durability:.0f}, catalyst {catalyst:.0f}, "
        f"and crowding risk {crowding:.0f}."
    )
    if warnings:
        summary += " Warnings: " + "; ".join(warnings) + "."

    return RadarAssessment(
        primary_setup=primary,
        secondary_setup=secondary,
        setup_score=round(setup_score, 2),
        urgency_score=round(urgency, 2),
        durability_score=round(durability, 2),
        catalyst_score=round(catalyst, 2),
        crowding_risk=round(crowding, 2),
        radar_adjustment=round(radar_adjustment, 2),
        position_multiplier=round(multiplier, 3),
        approved=approved,
        veto=veto,
        reasons=reasons,
        warnings=warnings,
        summary=summary,
    )
