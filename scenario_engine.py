from __future__ import annotations

import hashlib
import math
import os
import random
from dataclasses import asdict, dataclass
from typing import Any

SCENARIO_PATHS = max(500, int(os.getenv("SCENARIO_PATHS", "2500")))
SCENARIO_MAX_POSITION_MULTIPLIER = max(0.25, float(os.getenv("SCENARIO_MAX_POSITION_MULTIPLIER", "1.15")))
SCENARIO_MIN_PROBABILITY = min(0.75, max(0.40, float(os.getenv("SCENARIO_MIN_PROBABILITY", "0.52"))))
SCENARIO_MAX_TAIL_LOSS_PCT = min(0.25, max(0.02, float(os.getenv("SCENARIO_MAX_TAIL_LOSS_PCT", "0.12"))))


def _num(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return default if not math.isfinite(result) else result
    except (TypeError, ValueError):
        return default


def _value(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class ScenarioCase:
    name: str
    probability: float
    return_pct: float
    price_target: float | None
    explanation: str


@dataclass(frozen=True)
class ScenarioAssessment:
    horizon_days: int
    paths: int
    bull: ScenarioCase
    base: ScenarioCase
    bear: ScenarioCase
    probability_of_profit: float
    expected_return_pct: float
    median_return_pct: float
    value_at_risk_95_pct: float
    expected_shortfall_95_pct: float
    upside_capture_pct: float
    downside_risk_pct: float
    uncertainty_pct: float
    position_multiplier: float
    approved: bool
    veto: bool
    verdict: str
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _seed(symbol: str, market: str, quality: float, horizon: int) -> int:
    raw = f"{symbol}|{market}|{quality:.3f}|{horizon}".encode()
    return int(hashlib.sha256(raw).hexdigest()[:16], 16)


def assess_scenarios(
    signal: Any,
    *,
    quant: Any,
    memory: Any | None = None,
    market: str = "cash",
    horizon_days: int | None = None,
    paths: int = SCENARIO_PATHS,
) -> ScenarioAssessment:
    """Build bull/base/bear cases and a deterministic Monte Carlo risk envelope.

    This is a scenario model, not a promise or forecast. It combines the current
    signal, quant assessment, volatility, regime, costs and historical memory.
    """
    symbol = str(_value(signal, "symbol", "UNKNOWN"))
    price = max(0.0, _num(_value(signal, "price", _value(signal, "current_price", 0.0))))
    quality = _num(_value(quant, "trade_quality", 50.0), 50.0)
    alpha = _num(_value(quant, "alpha_score", quality), quality)
    risk = _num(_value(quant, "risk_score", 50.0), 50.0)
    net_ev = _num(_value(quant, "net_expected_value_pct", 0.0))
    costs = max(0.0, _num(_value(quant, "estimated_cost_pct", 0.0)))
    memory_adj = _num(_value(memory, "score_adjustment", 0.0))
    memory_win = _value(memory, "win_rate", None)
    memory_win = _num(memory_win, 0.5) if memory_win is not None else 0.5

    m20 = _num(_value(signal, "momentum_20d", 0.0))
    m5 = _num(_value(signal, "momentum_5d", 0.0))
    trend = _num(_value(signal, "trend_strength", 0.0))
    atr = max(0.004, _num(_value(signal, "atr_pct", 0.02), 0.02))
    annual_vol = max(0.08, _num(_value(signal, "volatility_20d", atr * 16.0), atr * 16.0))
    event_risk = _clip(_num(_value(signal, "event_risk_score", 20.0)), 0.0, 100.0)
    regime = str(_value(signal, "regime", "neutral") or "neutral").lower()

    if horizon_days is None:
        horizon_days = 5 if market == "crypto" else 7
    horizon_days = max(1, min(90, int(horizon_days)))
    paths = max(500, int(paths))

    regime_drift = {
        "bull": 0.0012, "risk-on": 0.0010, "neutral": 0.0002,
        "sideways": 0.0, "bear": -0.0010, "risk-off": -0.0011, "crisis": -0.0020,
    }.get(regime, 0.0001)
    signal_drift = 0.0028 * _clip((alpha - 50.0) / 50.0, -1.0, 1.0)
    signal_drift += 0.18 * m20 / max(horizon_days, 1) + 0.08 * m5 / max(horizon_days, 1)
    signal_drift += 0.08 * trend / max(horizon_days, 1)
    memory_drift = (memory_win - 0.5) * 0.0012 + memory_adj * 0.00008
    daily_drift = regime_drift + signal_drift + memory_drift + net_ev / max(horizon_days, 1) - costs / max(horizon_days, 1)
    daily_sigma = max(atr * 0.80, annual_vol / math.sqrt(252.0))
    daily_sigma *= 1.0 + event_risk / 260.0

    rng = random.Random(_seed(symbol, market, quality, horizon_days))
    returns: list[float] = []
    for _ in range(paths):
        total = 0.0
        volatility_state = daily_sigma
        for _day in range(horizon_days):
            shock = rng.gauss(0.0, volatility_state)
            # Light fat-tail mixture and volatility clustering.
            if rng.random() < 0.035:
                shock += rng.gauss(0.0, volatility_state * 2.4)
            total += daily_drift - 0.5 * volatility_state * volatility_state + shock
            volatility_state = _clip(0.86 * volatility_state + 0.14 * abs(shock), daily_sigma * 0.65, daily_sigma * 2.1)
        returns.append(math.exp(total) - 1.0)

    returns.sort()
    n = len(returns)
    q05 = returns[max(0, int(n * 0.05) - 1)]
    q25 = returns[max(0, int(n * 0.25) - 1)]
    q50 = returns[max(0, int(n * 0.50) - 1)]
    q75 = returns[max(0, int(n * 0.75) - 1)]
    q90 = returns[max(0, int(n * 0.90) - 1)]
    expected = sum(returns) / n
    probability = sum(1 for r in returns if r > 0.0) / n
    tail = returns[:max(1, int(n * 0.05))]
    expected_shortfall = sum(tail) / len(tail)
    uncertainty = max(0.0, q75 - q25)

    bull_prob = _clip(0.18 + 0.34 * probability + 0.0015 * max(0.0, quality - 68.0), 0.15, 0.55)
    bear_prob = _clip(0.18 + 0.34 * (1.0 - probability) + event_risk / 700.0, 0.15, 0.55)
    base_prob = max(0.05, 1.0 - bull_prob - bear_prob)
    total_prob = bull_prob + base_prob + bear_prob
    bull_prob, base_prob, bear_prob = (bull_prob / total_prob, base_prob / total_prob, bear_prob / total_prob)

    def target(ret: float) -> float | None:
        return round(price * (1.0 + ret), 6) if price > 0 else None

    bull = ScenarioCase("BULL", round(bull_prob, 4), round(q90, 6), target(q90), "Momentum persists, liquidity remains available, and the favorable regime holds.")
    base = ScenarioCase("BASE", round(base_prob, 4), round(q50, 6), target(q50), "Current edge partially realizes while normal volatility and trading costs remain in range.")
    bear = ScenarioCase("BEAR", round(bear_prob, 4), round(q05, 6), target(q05), "The setup fails, volatility expands, or event and regime risk overwhelm the signal.")

    probability_factor = _clip((probability - 0.45) / 0.25, 0.20, 1.15)
    tail_factor = _clip(1.0 - abs(expected_shortfall) / SCENARIO_MAX_TAIL_LOSS_PCT, 0.20, 1.0)
    uncertainty_factor = _clip(1.0 - uncertainty / 0.22, 0.35, 1.0)
    position_multiplier = _clip(probability_factor * tail_factor * uncertainty_factor, 0.20, SCENARIO_MAX_POSITION_MULTIPLIER)
    veto = probability < SCENARIO_MIN_PROBABILITY or abs(expected_shortfall) > SCENARIO_MAX_TAIL_LOSS_PCT or q50 <= -costs
    approved = not veto and expected > 0 and quality >= 68.0
    verdict = "FAVORABLE" if approved and probability >= 0.60 else ("BALANCED" if approved else "UNFAVORABLE")
    summary = (
        f"{verdict}: {probability:.0%} profitable paths over {horizon_days} days; expected {expected:+.2%}, "
        f"median {q50:+.2%}, 95% VaR {q05:+.2%}, tail loss {expected_shortfall:+.2%}; "
        f"scenario size multiplier {position_multiplier:.2f}."
    )
    return ScenarioAssessment(
        horizon_days, paths, bull, base, bear, round(probability * 100.0, 1),
        round(expected * 100.0, 2), round(q50 * 100.0, 2), round(q05 * 100.0, 2),
        round(expected_shortfall * 100.0, 2), round(max(0.0, q90) * 100.0, 2),
        round(abs(min(0.0, q05)) * 100.0, 2), round(uncertainty * 100.0, 2),
        round(position_multiplier, 4), approved, veto, verdict, summary,
    )
