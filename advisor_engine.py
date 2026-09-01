from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import math
from typing import Any

from config import ADVISOR_MODEL_VERSION, ADVISOR_RECOMMENDATION_TTL_MINUTES
from market_sessions import quote_is_fresh
from oracle_decision_identity import (
    ENTRY_ACTIONS as ORACLE_ENTRY_ACTIONS,
    ORACLE_ACTIONS,
    build_oracle_decision_identity,
    build_oracle_judgment,
    guard_oracle_action,
)
from probability_evidence import probability_metadata
from portfolio_optimizer import PortfolioConstraints, portfolio_fit_score
from provider_router import normalize_symbol
from strategy_engine import StrategySignal, ensemble_score, evaluate_strategies


# Legacy aliases remain accepted by the upstream scoring function, while the
# embedded Oracle judgment uses the shared canonical decision vocabulary.
ADVISOR_ACTIONS = set(ORACLE_ACTIONS) | {"ACCUMULATE", "WATCH"}
ENTRY_ACTIONS = set(ORACLE_ENTRY_ACTIONS) | {"ACCUMULATE"}


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
    oracle_judgment: dict[str, Any]
    generated_timestamp: str
    recommendation_expiration_timestamp: str
    labels: dict[str, str]
    oracle_decision: dict[str, Any]
    probability_evidence: dict[str, Any]

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


def _finite_positive(value: Any) -> bool:
    try:
        number = float(value)
        return math.isfinite(number) and number > 0.0
    except (TypeError, ValueError):
        return False


def _nonnegative_int(value: Any) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(number):
        return 0
    return max(0, int(number))


def _verified_quote(candidate: dict[str, Any], symbol: str) -> tuple[dict[str, Any], list[str]]:
    quote = dict(candidate.get("verified_quote") or candidate.get("quote") or {})
    for key in (
        "symbol",
        "requested_symbol",
        "provider_symbol",
        "provider",
        "price",
        "quote_timestamp",
        "interval",
        "quote_verified",
        "currency",
        "exchange",
    ):
        if key not in quote and key in candidate:
            quote[key] = candidate[key]
    missing: list[str] = []
    requested = normalize_symbol(quote.get("requested_symbol"))
    provider_symbol = normalize_symbol(quote.get("provider_symbol"))
    quote_symbol = normalize_symbol(quote.get("symbol") or symbol)
    if quote_symbol != symbol or requested != symbol or provider_symbol != symbol:
        missing.append("verified quote identity is missing or mismatched")
    if quote.get("quote_verified") is not True:
        missing.append("verified quote flag is missing")
    if not _finite_positive(quote.get("price")):
        missing.append("verified price is missing")
    if not quote_is_fresh(quote.get("quote_timestamp"), str(quote.get("interval") or "1d"), symbol=symbol):
        missing.append("verified quote is stale")
    return quote, missing


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
    quote, quote_missing = _verified_quote(candidate, symbol)
    price = float(quote.get("price") or 0.0) if not quote_missing else 0.0
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
    missing = list(quote_missing)
    if data_quality < 50:
        missing.append("data quality is low")
    liquidity_available = bool(
        candidate.get("liquidity_value")
        or candidate.get("avg_dollar_volume")
        or candidate.get("volume")
    )
    if not liquidity_available:
        missing.append("liquidity evidence is unavailable")
    validation_status = str(candidate.get("validation_status") or candidate.get("forecast_validation_status") or "shadow").lower()
    approved_forecast = validation_status == "approved" or bool(candidate.get("forecast_approved"))
    if not approved_forecast:
        missing.append("forecast is experimental or shadow-only")

    proposed_action = _action(opportunity, confidence, missing + ([] if fit >= 50 else fit_reasons), restricted)
    guard_gaps = list(missing)
    if fit < 50:
        guard_gaps.extend(fit_reasons or ["portfolio fit is below the advisor evidence floor"])
    positive_evidence: list[str] = []
    if not quote_missing:
        positive_evidence.append("Current price identity and freshness are verified.")
    if approved_forecast:
        positive_evidence.append("Forecast evidence is approved for advisor entry use.")
    if data_quality >= 50:
        positive_evidence.append("Data quality passes the advisor evidence floor.")
    if liquidity_available:
        positive_evidence.append("Liquidity evidence is available.")
    if fit >= 50:
        positive_evidence.append("Portfolio fit is not rejected by the optimizer.")

    guarded = guard_oracle_action(
        proposed_action,
        expected_return=expected_return,
        expected_downside=downside,
        risk_reward_ratio=risk_reward,
        opportunity_score=opportunity,
        confidence=confidence,
        quote_verified=not quote_missing and quote.get("quote_verified") is True,
        forecast_approved=approved_forecast,
        data_quality_score=data_quality,
        minimum_data_quality=50.0,
        liquidity_available=liquidity_available,
        restricted=restricted,
        evidence_gaps=guard_gaps,
        positive_evidence=positive_evidence,
    )

    # Keep the existing public advisor API stable: WATCH/HOLD remain valid display
    # states, while oracle_judgment.final_judgment records the canonical WAIT.
    # This is a presentation compatibility mapping only; it cannot promote an entry.
    if guarded.action == "WAIT":
        if proposed_action == "WATCH" or quote_missing or data_quality < 50:
            action = "WATCH"
        else:
            action = "HOLD"
    elif guarded.action == "AVOID" and proposed_action in ENTRY_ACTIONS and not restricted:
        action = "HOLD"
    else:
        action = guarded.action

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

    invalidation_conditions = list(
        candidate.get("thesis_invalidation_conditions")
        or ["Verified data becomes stale, price breaks risk level, or catalyst evidence disappears."]
    )
    risk_factors = list(candidate.get("risk_factors") or missing or ["Market risk and forecast uncertainty."])
    oracle_judgment = build_oracle_judgment(
        action_result=guarded,
        market_state=candidate.get("market_state") or candidate.get("regime"),
        continuation_case=candidate.get("continuation_case") or candidate.get("catalyst") or candidate.get("investment_thesis"),
        supporting_evidence=positive_evidence,
        contradicting_evidence=risk_factors,
        expected_upside_pct=expected_return,
        expected_downside_pct=downside,
        confidence=confidence,
        confidence_kind=str(candidate.get("confidence_kind") or "HEURISTIC_SCORE"),
        invalidation_conditions=invalidation_conditions,
        relative_opportunity=candidate.get("relative_opportunity") or f"opportunity={opportunity:.1f}/100; portfolio_fit={fit:.1f}/100",
    )
    oracle_decision = build_oracle_decision_identity(
        symbol=symbol,
        action=("AVOID" if restricted else proposed_action),
        opportunity_score=opportunity,
        confidence=confidence,
        expected_upside_pct=expected_return,
        expected_downside_pct=downside,
        risk_reward_ratio=risk_reward,
        data_quality_score=data_quality,
        catalyst=candidate.get("catalyst") or "No confirmed catalyst supplied.",
        thesis=candidate.get("investment_thesis") or "Evidence supports monitoring until verified price, forecast, and risk gates agree.",
        risk_factors=list(candidate.get("risk_factors") or []),
        invalidation_conditions=list(candidate.get("thesis_invalidation_conditions") or []),
        evidence_used=evidence,
        evidence_gaps=missing + ([] if fit >= 50 else fit_reasons),
        relative_rank=candidate.get("relative_rank"),
        alternatives_count=candidate.get("alternatives_count"),
    )
    probability_info = probability_metadata(
        field_name="confidence",
        value=confidence,
        source="advisor_engine.generate_recommendation",
        calibrated=False,
        model_backed=approved_forecast,
        sample_count=_nonnegative_int(candidate.get("validation_sample_count")),
        model=str(candidate.get("model") or ""),
        model_version=str(candidate.get("model_version") or ""),
        notes="Advisor confidence is a heuristic confidence score unless a separate calibrated probability field is supplied.",
    )
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
        risk_factors=risk_factors,
        thesis_invalidation_conditions=invalidation_conditions,
        data_quality_score=data_quality,
        model_version=ADVISOR_MODEL_VERSION,
        evidence_used=evidence,
        oracle_judgment=oracle_judgment,
        generated_timestamp=now.isoformat(),
        recommendation_expiration_timestamp=(now + timedelta(minutes=ADVISOR_RECOMMENDATION_TTL_MINUTES)).isoformat(),
        labels={
            "opportunity": f"{opportunity:.0f}/100",
            "risk": "high" if downside > expected_return else "moderate",
            "data_quality": f"{data_quality:.0f}/100",
            "forecast_quality": "approved" if approved_forecast else "shadow-only",
            "portfolio_fit": f"{fit:.0f}/100",
            "confidence_kind": str(candidate.get("confidence_kind") or "HEURISTIC_SCORE").upper(),
        },
        oracle_decision=oracle_decision,
        probability_evidence=probability_info,
    )