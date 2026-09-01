from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


STRATEGY_NAMES = [
    "momentum",
    "trend_following",
    "breakout",
    "mean_reversion",
    "oversold_recovery",
    "quality",
    "value",
    "growth",
    "earnings_momentum",
    "unusual_volume",
    "volatility_expansion",
    "sector_rotation",
    "macro_sensitivity",
    "news_catalyst",
    "insider_activity",
    "congressional_activity",
    "etf_flow",
    "options_flow",
    "whale_activity",
    "relative_strength",
    "global_market_dislocation",
]


PREMIUM_STRATEGIES = {
    "insider_activity",
    "congressional_activity",
    "options_flow",
    "whale_activity",
}

REQUIRED_EVIDENCE: dict[str, tuple[str, ...]] = {
    "momentum": ("momentum_5d", "momentum_20d"),
    "trend_following": ("trend_strength",),
    "breakout": ("breakout_level", "price"),
    "mean_reversion": ("mean_reversion_zscore",),
    "oversold_recovery": ("rsi",),
    "quality": ("quality_score",),
    "value": ("valuation_score",),
    "growth": ("growth_score",),
    "earnings_momentum": ("earnings_surprise_pct",),
    "unusual_volume": ("volume_ratio",),
    "volatility_expansion": ("volatility", "average_volatility"),
    "sector_rotation": ("sector_strength",),
    "macro_sensitivity": ("macro_score",),
    "news_catalyst": ("news_score", "verified_news_count"),
    "insider_activity": ("insider_activity",),
    "congressional_activity": ("congressional_activity",),
    "etf_flow": ("etf_flow",),
    "options_flow": ("options_flow",),
    "whale_activity": ("whale_activity",),
    "relative_strength": ("relative_strength",),
    "global_market_dislocation": ("global_dislocation_score",),
}


@dataclass
class StrategySignal:
    strategy: str
    score: float
    confidence: float
    available: bool
    message: str
    evidence: dict[str, Any]


def _metric(data: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        value = float(data.get(key, default) or default)
        return value if value == value else default
    except (TypeError, ValueError):
        return default


def _generic_strategy(name: str, data: dict[str, Any]) -> StrategySignal:
    required = REQUIRED_EVIDENCE.get(name, ())
    missing = [key for key in required if data.get(key) in (None, "", [], {})]
    if missing:
        return StrategySignal(
            name,
            0.0,
            0.0,
            False,
            "Required provider evidence unavailable",
            {"missing": missing},
        )
    momentum = _metric(data, "momentum_5d") + _metric(data, "momentum_20d")
    trend = _metric(data, "trend_strength")
    volume = min(3.0, _metric(data, "volume_ratio", 1.0)) / 3.0
    valuation = _metric(data, "valuation_score", 50.0) / 100.0
    catalyst = _metric(data, "catalyst_score", 0.0) / 100.0
    base = 50.0 + 20.0 * momentum + 18.0 * trend + 12.0 * volume + 10.0 * valuation + 10.0 * catalyst
    score = max(0.0, min(100.0, base))
    confidence = max(0.0, min(1.0, _metric(data, "data_quality_score", 70.0) / 100.0))
    return StrategySignal(
        name,
        score,
        confidence,
        True,
        "Configured provider evidence evaluated",
        {key: data.get(key) for key in required} | {"momentum": momentum, "trend": trend, "volume": volume},
    )


def _mean_reversion_strategy(data: dict[str, Any]) -> StrategySignal:
    symbol = str(data.get("symbol") or "").upper().strip()
    if not symbol.endswith("-USD"):
        return _generic_strategy("mean_reversion", data)
    if data.get("mean_reversion_available") is not True:
        return StrategySignal(
            "mean_reversion",
            0.0,
            0.0,
            False,
            "Short-horizon crypto reversal evidence unavailable",
            {
                "symbol": symbol,
                "mean_reversion_available": data.get("mean_reversion_available"),
            },
        )

    required = (
        "mean_reversion_zscore",
        "short_horizon_return",
        "mean_reversion_score",
        "mean_reversion_confidence",
    )
    missing = [key for key in required if data.get(key) in (None, "", [], {})]
    if missing:
        return StrategySignal(
            "mean_reversion",
            0.0,
            0.0,
            False,
            "Required short-horizon reversal evidence unavailable",
            {"missing": missing},
        )

    factor = max(-1.0, min(1.0, _metric(data, "mean_reversion_score")))
    score = max(5.0, min(95.0, 50.0 + 45.0 * factor))
    model_confidence = max(0.0, min(1.0, _metric(data, "mean_reversion_confidence")))
    data_quality = max(0.0, min(1.0, _metric(data, "data_quality_score", 70.0) / 100.0))
    confidence = min(model_confidence, data_quality)
    return StrategySignal(
        "mean_reversion",
        score,
        confidence,
        True,
        "Research-backed short-horizon crypto reversal factor evaluated",
        {
            "zscore": data.get("mean_reversion_zscore"),
            "horizon_return": data.get("short_horizon_return"),
            "factor": factor,
            "side": data.get("mean_reversion_side"),
            "horizon_minutes": data.get("mean_reversion_horizon_minutes"),
            "displacement_bps": data.get("mean_reversion_displacement_bps"),
        },
    )


STRATEGY_REGISTRY: dict[str, Callable[[dict[str, Any]], StrategySignal]] = {
    name: (lambda data, strategy=name: _generic_strategy(strategy, data))
    for name in STRATEGY_NAMES
}
STRATEGY_REGISTRY["mean_reversion"] = _mean_reversion_strategy


def evaluate_strategies(data: dict[str, Any], enabled: list[str] | None = None) -> list[StrategySignal]:
    names = enabled or STRATEGY_NAMES
    return [STRATEGY_REGISTRY[name](data) for name in names if name in STRATEGY_REGISTRY]


def ensemble_score(signals: list[StrategySignal], portfolio_fit: float = 50.0, data_quality: float = 50.0) -> dict[str, float]:
    available = [signal for signal in signals if signal.available]
    if not available:
        return {
            "expected_return": 0.0,
            "downside_risk": 100.0,
            "liquidity": 0.0,
            "catalyst_strength": 0.0,
            "timing": 0.0,
            "trend_quality": 0.0,
            "valuation": 0.0,
            "portfolio_fit": portfolio_fit,
            "data_quality": data_quality,
            "model_agreement": 0.0,
            "overall_confidence": 0.0,
        }
    average_score = sum(signal.score for signal in available) / len(available)
    confidence = sum(signal.confidence for signal in available) / len(available)
    dispersion = sum(abs(signal.score - average_score) for signal in available) / len(available)
    agreement = max(0.0, 100.0 - dispersion)
    return {
        "expected_return": average_score,
        "downside_risk": max(0.0, 100.0 - average_score),
        "liquidity": min(100.0, data_quality),
        "catalyst_strength": average_score,
        "timing": average_score,
        "trend_quality": average_score,
        "valuation": average_score,
        "portfolio_fit": portfolio_fit,
        "data_quality": data_quality,
        "model_agreement": agreement,
        "overall_confidence": max(0.0, min(100.0, confidence * agreement)),
    }
