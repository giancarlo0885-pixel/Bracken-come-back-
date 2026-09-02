from __future__ import annotations

from typing import Any

import runtime_integrity_patch as patch


def _score_meets_threshold(score: Any, threshold: Any) -> bool:
    """Compare equivalent 0..1 and 0..100 score representations.

    Oracle Council emits normalized 0..1 scores, while the legacy worker quality
    threshold remains configured on a 0..100 scale. This conversion changes no
    threshold: 0.52 is treated as 52, 0.70 as 70, etc.
    """
    score_value = patch._numeric(score)
    threshold_value = patch._numeric(threshold)
    if 0.0 <= score_value <= 1.0 and threshold_value > 1.0:
        score_value *= 100.0
    elif score_value > 1.0 and 0.0 <= threshold_value <= 1.0:
        threshold_value *= 100.0
    return score_value >= threshold_value


def install_core_rebalance_score_compat() -> None:
    original = patch._core_rebalance_candidate_allowed
    if getattr(original, "_oracle_score_scale_compatible", False):
        return

    def candidate_allowed(
        signal: Any,
        *,
        market: str,
        deployment_gap: float,
        score_threshold: float,
        confidence_threshold: float,
    ) -> bool:
        if str(market or "").strip().lower() != "crypto":
            return False
        if deployment_gap <= 0:
            return False
        if str(patch._signal_value(signal, "action", "HOLD") or "HOLD").strip().upper() != "HOLD":
            return False

        # Score-scale compatibility must not broaden authorization semantics.
        # Only an upstream portfolio producer may authorize a core-rebalance
        # candidate. A plain tactical HOLD is never inferred to be a candidate.
        existing_intent = patch._core_rebalance_intent(signal)
        if existing_intent not in {
            patch.CORE_REBALANCE_CANDIDATE_INTENT,
            patch.CORE_REBALANCE_STRATEGIC_CANDIDATE_INTENT,
        }:
            return False

        score = patch._signal_value(signal, "score", 0.0)
        confidence = patch._numeric(patch._signal_value(signal, "confidence", 0.0))
        return _score_meets_threshold(score, score_threshold) and confidence >= patch._numeric(confidence_threshold)

    candidate_allowed._oracle_score_scale_compatible = True
    patch._core_rebalance_candidate_allowed = candidate_allowed
