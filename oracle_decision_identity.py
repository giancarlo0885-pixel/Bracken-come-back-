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
