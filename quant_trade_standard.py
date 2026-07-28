from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return default if result != result else result
    except (TypeError, ValueError):
        return default


def _value(signal: Any, name: str, default: Any = None) -> Any:
    if isinstance(signal, dict):
        return signal.get(name, default)
    return getattr(signal, name, default)


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class QuantTradeAssessment:
    alpha_score: float
    execution_score: float
    risk_score: float
    relative_value_score: float
    trade_quality: float
    gross_expected_value_pct: float
    estimated_cost_pct: float
    net_expected_value_pct: float
    adverse_selection_score: float
    position_multiplier: float
    approved: bool
    classification: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_trade(
    signal: Any,
    *,
    market: str = "cash",
    portfolio_concentration: float = 0.0,
    min_quality: float = 68.0,
    min_net_ev_pct: float = 0.001,
    max_spread_pct: float = 0.006,
    max_slippage_pct: float = 0.005,
) -> QuantTradeAssessment:
    """Score a proposed trade using portable institutional-quant principles.

    This is intentionally usable without Level-2 order-book data. When richer
    fields are present on the signal, they automatically improve the estimate.
    All percentages are decimal values (0.01 == 1%).
    """
    raw_score = _number(_value(signal, "score", 0.5), 0.5)
    score = raw_score * 100.0 if raw_score <= 1.0 else raw_score
    raw_confidence = _number(_value(signal, "confidence", 0.5), 0.5)
    confidence = raw_confidence * 100.0 if raw_confidence <= 1.0 else raw_confidence

    m5 = _number(_value(signal, "momentum_5d", 0.0))
    m20 = _number(_value(signal, "momentum_20d", 0.0))
    trend = _number(_value(signal, "trend_strength", 0.0))
    volume_ratio = max(0.0, _number(_value(signal, "volume_ratio", 1.0), 1.0))
    volatility = max(0.0, _number(_value(signal, "volatility_20d", 0.35), 0.35))
    atr_pct = max(0.0, _number(_value(signal, "atr_pct", volatility / 16.0), volatility / 16.0))
    news = _number(_value(signal, "news_sentiment", 0.0))
    regime = str(_value(signal, "regime", "neutral") or "neutral").lower()

    relative_strength = _number(
        _value(signal, "relative_strength", 0.65 * m20 + 0.35 * m5)
    )
    spread_pct = max(
        0.0,
        _number(_value(signal, "spread_pct", 0.0008 if market == "cash" else 0.0018)),
    )
    slippage_pct = max(
        0.0,
        _number(_value(signal, "estimated_slippage_pct", spread_pct * 0.75)),
    )
    fees_pct = max(
        0.0,
        _number(_value(signal, "estimated_fees_pct", 0.0002 if market == "cash" else 0.0010)),
    )
    impact_pct = max(
        0.0,
        _number(_value(signal, "estimated_market_impact_pct", max(0.0, 1.0 - volume_ratio) * 0.0015)),
    )
    event_risk = _clip(_number(_value(signal, "event_risk_score", 20.0)), 0.0, 100.0)
    order_imbalance = _clip(_number(_value(signal, "order_book_imbalance", 0.0)), -1.0, 1.0)

    alpha = _clip(
        0.48 * score
        + 0.22 * confidence
        + 12.0 * _clip(m20 / 0.12, -1.0, 1.0)
        + 7.0 * _clip(m5 / 0.06, -1.0, 1.0)
        + 7.0 * _clip(trend / 0.06, -1.0, 1.0)
        + 4.0 * _clip(news, -1.0, 1.0)
    )

    liquidity_bonus = 12.0 * _clip((volume_ratio - 0.7) / 1.3, 0.0, 1.0)
    execution = _clip(
        92.0
        + liquidity_bonus
        + 5.0 * max(0.0, order_imbalance)
        - 4200.0 * spread_pct
        - 3000.0 * slippage_pct
        - 1600.0 * impact_pct
    )

    regime_penalty = 18.0 if regime in {"risk-off", "bear", "crisis"} else 0.0
    concentration_penalty = 28.0 * _clip(portfolio_concentration, 0.0, 1.0)
    risk = _clip(
        96.0
        - 46.0 * _clip(volatility / 1.20, 0.0, 1.0)
        - 380.0 * _clip(atr_pct, 0.0, 0.12)
        - 0.20 * event_risk
        - regime_penalty
        - concentration_penalty
    )

    relative_value = _clip(
        50.0
        + 32.0 * _clip(relative_strength / 0.15, -1.0, 1.0)
        + 8.0 * _clip((volume_ratio - 1.0) / 1.5, -1.0, 1.0)
        + 10.0 * _clip(news, -1.0, 1.0)
    )

    trade_quality = _clip(
        0.35 * alpha + 0.25 * execution + 0.25 * risk + 0.15 * relative_value
    )

    probability_win = _clip(0.35 + 0.0045 * alpha + 0.0015 * relative_value, 0.38, 0.78)
    expected_gain_pct = max(atr_pct * 2.2, 0.012 + max(0.0, m20) * 0.35)
    expected_loss_pct = max(atr_pct * 1.25, 0.008)
    gross_ev = probability_win * expected_gain_pct - (1.0 - probability_win) * expected_loss_pct
    estimated_cost = spread_pct + slippage_pct + fees_pct + impact_pct
    net_ev = gross_ev - estimated_cost

    adverse = _clip(
        20.0
        + 0.45 * event_risk
        + 34.0 * _clip(volatility / 1.5, 0.0, 1.0)
        + 2500.0 * max(0.0, spread_pct - max_spread_pct * 0.5)
        + 1800.0 * max(0.0, slippage_pct - max_slippage_pct * 0.5)
        + (15.0 if regime in {"risk-off", "crisis"} else 0.0)
        - 10.0 * max(0.0, order_imbalance)
    )

    approved = (
        trade_quality >= min_quality
        and net_ev >= min_net_ev_pct
        and spread_pct <= max_spread_pct
        and slippage_pct <= max_slippage_pct
        and adverse < 70.0
    )

    if trade_quality >= 90.0:
        classification = "ELITE QUANT SETUP"
    elif trade_quality >= 82.0:
        classification = "HIGH-QUALITY SETUP"
    elif trade_quality >= 75.0:
        classification = "CONDITIONAL SETUP"
    elif trade_quality >= 68.0:
        classification = "WATCH / RESEARCH"
    else:
        classification = "REJECTED"

    quality_factor = _clip((trade_quality - 60.0) / 35.0, 0.25, 1.15)
    ev_factor = _clip(net_ev / 0.012, 0.25, 1.15)
    adverse_factor = _clip(1.0 - adverse / 110.0, 0.30, 1.0)
    position_multiplier = _clip(quality_factor * ev_factor * adverse_factor, 0.20, 1.10)
    if not approved:
        position_multiplier = min(position_multiplier, 0.35)

    reason = (
        f"quant={trade_quality:.1f}; alpha={alpha:.1f}; execution={execution:.1f}; "
        f"risk={risk:.1f}; relative={relative_value:.1f}; netEV={net_ev:.2%}; "
        f"cost={estimated_cost:.2%}; adverse={adverse:.1f}; {classification}"
    )

    return QuantTradeAssessment(
        round(alpha, 2), round(execution, 2), round(risk, 2),
        round(relative_value, 2), round(trade_quality, 2),
        round(gross_ev, 6), round(estimated_cost, 6), round(net_ev, 6),
        round(adverse, 2), round(position_multiplier, 4), approved,
        classification, reason,
    )
