from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any

from config import ADVISOR_MODEL_VERSION, ADVISOR_RECOMMENDATION_TTL_MINUTES
from portfolio_optimizer import PortfolioConstraints, portfolio_fit_score
from provider_router import normalize_symbol
from strategy_engine import StrategySignal, ensemble_score, evaluate_strategies


ADVISOR_ACTIONS = {"STRONG BUY", "BUY", "ACCUMULATE", "HOLD", "REDUCE", "SELL", "AVOID", "WATCH"}


@dataclass
class AdvisorProfile:
    investment_objective: str = "balanced growth"
    risk_tolerance: str = "moderate"
    investment_horizon: str = "6-18 months"
    available_capital: float = 0.0
    liquidity_needs: float = 0.0
    maximum_acceptable_drawdown: float = 0.12
    restricted_assets: list[str] = field(default_factory=list)
    preferred_asset_classes: list[str] = field(default_factory=lambda: ["stock", "etf", "crypto"])


@dataclass
class AdvisorRecommendation:
    recommendation_id: str
    symbol: str
    company_or_asset_name: str
    market: str
    exchange: str
    currency: str
    current_verified_price: float
    action: str
    confidence: float
    opportunity_score: float
    expected_return: float
    expected_downside: float
    risk_reward_ratio: float
    holding_horizon: str
    suggested_entry_range: tuple[float, float]
    stop_loss_level: float
    first_profit_target: float
    second_profit_target: float
    suggested_position_size: float
    catalyst: str
    investment_thesis: str
    risk_factors: list[str]
    thesis_invalidation_conditions: list[str]
    data_quality_score: float
    model_version: str
    evidence_used: list[dict[str, Any]]
    generated_timestamp: str
    recommendation_expiration_timestamp: str
    labels: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _action(score: float, confidence: float, risk_notes: list[str], restricted: bool) -> str:
    if restricted:
        return "AVOID"
    if risk_notes and score < 70:
        return "WATCH"
    if score >= 88 and confidence >= 80:
        return "STRONG BUY"
    if score >= 75 and confidence >= 65:
        return "BUY"
    if score >= 62 and confidence >= 55:
        return "ACCUMULATE"
    if score <= 25:
        return "SELL"
    if score <= 40:
        return "REDUCE"
    return "HOLD"


def generate_recommendation(
    candidate: dict[str, Any],
    profile: AdvisorProfile | None = None,
    *,
    holdings: list[dict[str, Any]] | None = None,
    strategy_signals: list[StrategySignal] | None = None,
) -> AdvisorRecommendation:
    profile = profile or AdvisorProfile()
    holdings = list(holdings or [])
    symbol = normalize_symbol(candidate.get("symbol"))
    price = float(candidate.get("current_verified_price") or candidate.get("price") or 0.0)
    data_quality = float(candidate.get("data_quality_score") or 0.0)
    strategies = strategy_signals or evaluate_strategies(candidate)
    fit, fit_reasons = portfolio_fit_score(
        symbol=symbol,
        candidate={
            **candidate,
            "portfolio_equity": max(profile.available_capital, 1.0),
            "suggested_value": float(candidate.get("suggested_position_size") or 0.0),
        },
        holdings=holdings,
        constraints=PortfolioConstraints(),
    )
    ensemble = ensemble_score(strategies, portfolio_fit=fit, data_quality=data_quality)
    opportunity = max(0.0, min(100.0, float(candidate.get("opportunity_score") or ensemble["expected_return"])))
    confidence = max(0.0, min(100.0, float(candidate.get("confidence") or ensemble["overall_confidence"])))
    expected_return = float(candidate.get("expected_return") or candidate.get("expected_move_pct") or 0.0)
    downside = abs(float(candidate.get("expected_downside") or candidate.get("volatility") or 0.0))
    risk_reward = expected_return / downside if downside > 0 else 0.0
    restricted = symbol in {normalize_symbol(item) for item in profile.restricted_assets}
    missing = []
    if price <= 0:
        missing.append("verified price is missing")
    if data_quality < 50:
        missing.append("data quality is low")
    action = _action(opportunity, confidence, missing + ([] if fit >= 50 else fit_reasons), restricted)
    if price <= 0:
        action = "WATCH" if not restricted else "AVOID"
    entry_low = price * 0.995 if price > 0 else 0.0
    entry_high = price * 1.005 if price > 0 else 0.0
    now = datetime.now(timezone.utc)
    rec_id = hashlib.sha256(f"{symbol}|{now.isoformat()}|{ADVISOR_MODEL_VERSION}".encode("utf-8")).hexdigest()[:24]
    evidence = [
        {"type": "information", "summary": "Recommendation uses only supplied application/provider data."},
        {"type": "forecast", "summary": str(candidate.get("forecast_summary") or "Forecast evidence is unavailable or model-generated from provided bars.")},
        {"type": "opinion", "summary": str(candidate.get("investment_thesis") or "The advisor score reflects a weighted strategy and portfolio-fit view.")},
        {"type": "risk_warning", "summary": "Profits are not guaranteed; losses can exceed expectations in fast markets."},
    ]
    for item in missing:
        evidence.append({"type": "missing_data", "summary": item})
    return AdvisorRecommendation(
        recommendation_id=rec_id,
        symbol=symbol,
        company_or_asset_name=str(candidate.get("name") or candidate.get("company") or symbol),
        market=str(candidate.get("market") or "cash"),
        exchange=str(candidate.get("exchange") or ""),
        currency=str(candidate.get("currency") or "USD"),
        current_verified_price=price,
        action=action,
        confidence=confidence,
        opportunity_score=opportunity,
        expected_return=expected_return,
        expected_downside=downside,
        risk_reward_ratio=risk_reward,
        holding_horizon=str(candidate.get("holding_horizon") or profile.investment_horizon),
        suggested_entry_range=(entry_low, entry_high),
        stop_loss_level=price * 0.94 if price > 0 else 0.0,
        first_profit_target=price * (1 + max(expected_return, 2.0) / 100.0) if price > 0 else 0.0,
        second_profit_target=price * (1 + max(expected_return * 1.6, 4.0) / 100.0) if price > 0 else 0.0,
        suggested_position_size=float(candidate.get("suggested_position_size") or min(profile.available_capital * 0.05, profile.available_capital)),
        catalyst=str(candidate.get("catalyst") or "No confirmed catalyst supplied."),
        investment_thesis=str(candidate.get("investment_thesis") or "Evidence supports monitoring until verified price, forecast, and risk gates agree."),
        risk_factors=list(candidate.get("risk_factors") or missing or ["Market risk and forecast uncertainty."]),
        thesis_invalidation_conditions=list(candidate.get("thesis_invalidation_conditions") or ["Verified data becomes stale, price breaks risk level, or catalyst evidence disappears."]),
        data_quality_score=data_quality,
        model_version=ADVISOR_MODEL_VERSION,
        evidence_used=evidence,
        generated_timestamp=now.isoformat(),
        recommendation_expiration_timestamp=(now + timedelta(minutes=ADVISOR_RECOMMENDATION_TTL_MINUTES)).isoformat(),
        labels={
            "opportunity": f"{opportunity:.0f}/100",
            "risk": "high" if downside > expected_return else "moderate",
            "data_quality": f"{data_quality:.0f}/100",
            "forecast_quality": str(candidate.get("validation_status") or "unvalidated"),
            "portfolio_fit": f"{fit:.0f}/100",
        },
    )
