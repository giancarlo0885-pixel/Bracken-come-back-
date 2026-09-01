from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Iterable


ORACLE_ACTIONS = frozenset(
    {
        "STRONG BUY",
        "BUY",
        "HOLD",
        "WAIT",
        "AVOID",
        "REDUCE",
        "SELL",
        "UNKNOWN",
    }
)
ENTRY_ACTIONS = frozenset({"STRONG BUY", "BUY"})
ACTION_ALIASES = {
    "STRONG_BUY": "STRONG BUY",
    "ACCUMULATE": "BUY",
    "WATCH": "WAIT",
    "NO TRADE": "WAIT",
    "NO_TRADE": "WAIT",
    "INSUFFICIENT EVIDENCE": "UNKNOWN",
}


def canonical_oracle_action(value: Any) -> str:
    text = " ".join(str(value or "UNKNOWN").upper().replace("_", " ").split())
    text = ACTION_ALIASES.get(text, text)
    return text if text in ORACLE_ACTIONS else "UNKNOWN"


def _finite(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clean(items: Iterable[Any] | None) -> list[str]:
    result: list[str] = []
    for item in items or []:
        text = " ".join(str(item or "").split()).strip()
        if text and text not in result:
            result.append(text)
    return result


@dataclass(frozen=True)
class OracleIdentityResult:
    proposed_action: str
    action: str
    entry_allowed: bool
    positive_evidence: list[str]
    contradictions: list[str]
    unknowns: list[str]
    reason_codes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def guard_oracle_action(
    proposed_action: Any,
    *,
    expected_return: Any = None,
    expected_downside: Any = None,
    risk_reward_ratio: Any = None,
    opportunity_score: Any = None,
    confidence: Any = None,
    quote_verified: bool | None = None,
    forecast_approved: bool | None = None,
    data_quality_score: Any = None,
    minimum_data_quality: float = 50.0,
    liquidity_available: bool | None = None,
    restricted: bool = False,
    evidence_gaps: Iterable[Any] | None = None,
    contradicting_evidence: Iterable[Any] | None = None,
    positive_evidence: Iterable[Any] | None = None,
) -> OracleIdentityResult:
    """Protect the Oracle BUY identity without creating a new alpha threshold.

    Existing upstream scoring still decides whether an opportunity is strong enough
    to propose an entry. This guard only prevents that proposal from surviving when
    critical evidence is missing, explicitly negative, or contradicted. It never
    upgrades WAIT/HOLD/AVOID/SELL into BUY.
    """

    proposed = canonical_oracle_action(proposed_action)
    action = proposed
    positives = _clean(positive_evidence)
    contradictions = _clean(contradicting_evidence)
    unknowns = _clean(evidence_gaps)
    reason_codes: list[str] = []

    if restricted:
        contradictions.append("Asset is restricted by the active advisor profile.")
        reason_codes.append("RESTRICTED_ASSET")
        action = "AVOID"

    if proposed in ENTRY_ACTIONS and not restricted:
        if quote_verified is not True:
            unknowns.append("Executable market quote is not verified.")
            reason_codes.append("QUOTE_NOT_VERIFIED")
        if forecast_approved is not True:
            unknowns.append("Forecast model is not approved for entry use.")
            reason_codes.append("FORECAST_NOT_APPROVED")
        quality = _finite(data_quality_score)
        if quality is None:
            unknowns.append("Data quality is unknown.")
            reason_codes.append("DATA_QUALITY_UNKNOWN")
        elif quality < minimum_data_quality:
            unknowns.append(f"Data quality {quality:.1f} is below the required evidence floor {minimum_data_quality:.1f}.")
            reason_codes.append("DATA_QUALITY_LOW")
        if liquidity_available is not True:
            unknowns.append("Liquidity evidence is unavailable.")
            reason_codes.append("LIQUIDITY_UNKNOWN")

        upside = _finite(expected_return)
        if upside is None:
            unknowns.append("Expected upside is unavailable.")
            reason_codes.append("UPSIDE_UNKNOWN")
        elif upside <= 0:
            contradictions.append("Expected return is not positive.")
            reason_codes.append("NON_POSITIVE_EXPECTED_RETURN")
        else:
            positives.append("Expected return is positive.")

        rr = _finite(risk_reward_ratio)
        if rr is None:
            unknowns.append("Risk/reward estimate is unavailable.")
            reason_codes.append("RISK_REWARD_UNKNOWN")
        elif rr <= 0:
            contradictions.append("Risk/reward estimate is not positive.")
            reason_codes.append("RISK_REWARD_NON_POSITIVE")

        score = _finite(opportunity_score)
        if score is None:
            unknowns.append("Opportunity score is unavailable.")
            reason_codes.append("OPPORTUNITY_SCORE_UNKNOWN")
        conf = _finite(confidence)
        if conf is None:
            unknowns.append("Confidence evidence is unavailable.")
            reason_codes.append("CONFIDENCE_UNKNOWN")

        if contradictions:
            action = "AVOID" if "NON_POSITIVE_EXPECTED_RETURN" in reason_codes else "WAIT"
        elif unknowns:
            action = "WAIT"

    # A guard is allowed to downgrade an entry, never to promote a non-entry.
    if proposed not in ENTRY_ACTIONS and action in ENTRY_ACTIONS:
        action = proposed
        reason_codes.append("NO_GUARD_PROMOTION")

    positives = _clean(positives)
    contradictions = _clean(contradictions)
    unknowns = _clean(unknowns)
    return OracleIdentityResult(
        proposed_action=proposed,
        action=action,
        entry_allowed=action in ENTRY_ACTIONS and not contradictions and not unknowns,
        positive_evidence=positives,
        contradictions=contradictions,
        unknowns=unknowns,
        reason_codes=list(dict.fromkeys(reason_codes)),
    )


def build_oracle_judgment(
    *,
    action_result: OracleIdentityResult,
    market_state: Any = None,
    continuation_case: Any = None,
    supporting_evidence: Iterable[Any] | None = None,
    contradicting_evidence: Iterable[Any] | None = None,
    expected_upside_pct: Any = None,
    expected_downside_pct: Any = None,
    confidence: Any = None,
    confidence_kind: str = "HEURISTIC_SCORE",
    invalidation_conditions: Iterable[Any] | None = None,
    relative_opportunity: Any = None,
) -> dict[str, Any]:
    """Return the explicit ten-question Oracle judgment payload.

    Unknown inputs stay visibly unknown. The payload does not manufacture a bullish
    explanation or convert a heuristic confidence score into a calibrated probability.
    """

    upside = _finite(expected_upside_pct)
    downside = _finite(expected_downside_pct)
    certainty = _finite(confidence)
    contradictions = _clean(contradicting_evidence) + action_result.contradictions + action_result.unknowns
    support = _clean(supporting_evidence) + action_result.positive_evidence
    return {
        "what_is_happening": str(market_state).strip() if market_state not in (None, "") else "UNKNOWN",
        "why_might_it_continue": str(continuation_case).strip() if continuation_case not in (None, "") else "UNKNOWN",
        "supporting_evidence": _clean(support),
        "contradicting_evidence": _clean(contradictions),
        "estimated_upside_pct": upside,
        "estimated_downside_pct": downside,
        "certainty": {
            "value": certainty,
            "kind": str(confidence_kind or "UNVALIDATED").upper(),
            "is_calibrated_probability": str(confidence_kind or "").upper() == "CALIBRATED_PROBABILITY",
        },
        "invalidation_conditions": _clean(invalidation_conditions),
        "relative_opportunity": str(relative_opportunity).strip() if relative_opportunity not in (None, "") else "UNKNOWN",
        "final_judgment": {
            "action": action_result.action,
            "proposed_action": action_result.proposed_action,
            "entry_allowed": action_result.entry_allowed,
            "reason_codes": action_result.reason_codes,
        },
    }


PRIMARY_JUDGMENTS = tuple(action for action in ORACLE_ACTIONS if action != "HOLD")
ENTRY_JUDGMENTS = ENTRY_ACTIONS
UNKNOWN_MARKERS = (
    "missing",
    "unavailable",
    "unknown",
    "insufficient",
    "cannot verify",
    "unverified",
)


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _number(value: Any, default: float = 0.0) -> float:
    result = _finite(value)
    return result if result is not None else default


def canonical_oracle_judgment(action: Any) -> str:
    """Compatibility alias for the Oracle's canonical user-facing vocabulary."""
    value = canonical_oracle_action(action)
    return "WAIT" if value == "HOLD" else value


def buy_identity_failures(
    *,
    opportunity_score: Any,
    confidence: Any,
    expected_upside_pct: Any,
    expected_downside_pct: Any,
    risk_reward_ratio: Any,
    data_quality_score: Any,
    evidence_gaps: list[str] | None = None,
    min_opportunity_score: float = 62.0,
    min_confidence: float = 55.0,
    min_data_quality: float = 50.0,
    min_risk_reward: float = 1.15,
) -> list[str]:
    """Return the reasons an entry judgment is not yet a positive opportunity."""
    gaps = [str(item) for item in evidence_gaps or [] if str(item or "").strip()]
    failures: list[str] = []
    upside = _number(expected_upside_pct)
    downside = abs(_number(expected_downside_pct))
    rr = _number(risk_reward_ratio)
    if upside <= 0:
        failures.append("estimated upside is not positive")
    if downside <= 0:
        failures.append("estimated downside is unavailable")
    if rr < min_risk_reward:
        failures.append("risk/reward is not asymmetric enough")
    if _number(opportunity_score) < min_opportunity_score:
        failures.append("opportunity score is below the Oracle entry standard")
    if _number(confidence) < min_confidence:
        failures.append("validated confidence is too low")
    if _number(data_quality_score) < min_data_quality:
        failures.append("data quality is too weak")
    if gaps:
        failures.extend(gaps)
    return failures


def action_preserves_oracle_buy_identity(
    action: Any,
    *,
    opportunity_score: Any,
    confidence: Any,
    expected_upside_pct: Any,
    expected_downside_pct: Any,
    risk_reward_ratio: Any,
    data_quality_score: Any,
    evidence_gaps: list[str] | None = None,
) -> tuple[bool, list[str]]:
    judgment = canonical_oracle_judgment(action)
    if judgment not in ENTRY_JUDGMENTS:
        return True, []
    failures = buy_identity_failures(
        opportunity_score=opportunity_score,
        confidence=confidence,
        expected_upside_pct=expected_upside_pct,
        expected_downside_pct=expected_downside_pct,
        risk_reward_ratio=risk_reward_ratio,
        data_quality_score=data_quality_score,
        evidence_gaps=evidence_gaps,
    )
    return not failures, failures


def build_oracle_decision_identity(
    *,
    symbol: Any,
    action: Any,
    opportunity_score: Any,
    confidence: Any,
    expected_upside_pct: Any,
    expected_downside_pct: Any,
    risk_reward_ratio: Any,
    data_quality_score: Any,
    catalyst: Any = None,
    thesis: Any = None,
    risk_factors: list[str] | None = None,
    invalidation_conditions: list[str] | None = None,
    evidence_used: list[dict[str, Any]] | None = None,
    evidence_gaps: list[str] | None = None,
    relative_rank: Any = None,
    alternatives_count: Any = None,
) -> dict[str, Any]:
    """Build the legacy ten-question decision payload from guarded evidence."""
    gaps = [str(item) for item in evidence_gaps or [] if str(item or "").strip()]
    _, identity_failures = action_preserves_oracle_buy_identity(
        action,
        opportunity_score=opportunity_score,
        confidence=confidence,
        expected_upside_pct=expected_upside_pct,
        expected_downside_pct=expected_downside_pct,
        risk_reward_ratio=risk_reward_ratio,
        data_quality_score=data_quality_score,
        evidence_gaps=gaps,
    )
    guarded = guard_oracle_action(
        action,
        expected_return=expected_upside_pct,
        expected_downside=expected_downside_pct,
        risk_reward_ratio=risk_reward_ratio,
        opportunity_score=opportunity_score,
        confidence=confidence,
        quote_verified=not any("quote" in gap.lower() for gap in gaps),
        forecast_approved=not any("forecast" in gap.lower() for gap in gaps),
        data_quality_score=data_quality_score,
        liquidity_available=not any("liquidity" in gap.lower() for gap in gaps),
        evidence_gaps=gaps,
    )
    judgment = canonical_oracle_judgment(guarded.action)
    if judgment == "AVOID" and canonical_oracle_judgment(action) in ENTRY_JUDGMENTS:
        judgment = "WAIT"
    if gaps and any(any(marker in gap.lower() for marker in UNKNOWN_MARKERS) for gap in gaps):
        if judgment in {"WAIT", "BUY", "STRONG BUY"}:
            judgment = "UNKNOWN"

    support = []
    if _number(expected_upside_pct) > 0:
        support.append(f"Estimated upside is {_number(expected_upside_pct):.2f}%.")
    if _number(risk_reward_ratio) >= 1.15:
        support.append(f"Risk/reward is {_number(risk_reward_ratio):.2f} to 1.")
    if _number(opportunity_score) >= 62:
        support.append(f"Opportunity score is {_number(opportunity_score):.1f}/100.")
    if _number(confidence) >= 55:
        support.append(f"Validated confidence is {_number(confidence):.1f}%.")
    for item in evidence_used or []:
        summary = _text(item.get("summary") if isinstance(item, dict) else item)
        evidence_type = _text(item.get("type") if isinstance(item, dict) else "")
        if summary and evidence_type not in {"risk_warning", "missing_data"}:
            support.append(summary)

    contradictions = list(risk_factors or [])
    contradictions.extend(gaps)
    contradictions.extend(identity_failures)
    contradictions.extend(guarded.contradictions)
    contradictions.extend(guarded.unknowns)
    if not contradictions:
        contradictions.append("No material contradiction was supplied by the current evidence set.")

    invalidation = list(invalidation_conditions or [])
    if not invalidation:
        invalidation = [
            "Verified quote freshness is lost.",
            "The expected move or risk/reward turns negative.",
            "A stronger alternative displaces this setup.",
        ]

    rank_text = "Alternative ranking evidence was not supplied."
    rank = _number(relative_rank)
    count = int(_number(alternatives_count))
    if rank > 0 and count > 0:
        rank_text = f"Ranked {int(rank)} of {count} currently supplied alternatives."

    return {
        "vocabulary": list(PRIMARY_JUDGMENTS),
        "symbol": _text(symbol),
        "what_is_happening": _text(catalyst, "UNKNOWN"),
        "why_might_it_continue": _text(thesis, "UNKNOWN"),
        "supporting_evidence": _clean(support),
        "contradicting_evidence": _clean(contradictions),
        "estimated_upside": f"{_number(expected_upside_pct):.2f}%",
        "estimated_downside": f"{abs(_number(expected_downside_pct)):.2f}%",
        "certainty": f"{_number(confidence):.1f}% confidence, data quality {_number(data_quality_score):.1f}/100",
        "thesis_invalidation": _clean(invalidation),
        "relative_opportunity": rank_text,
        "final_judgment": judgment,
    }
