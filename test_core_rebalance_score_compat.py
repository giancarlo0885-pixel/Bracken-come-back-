from types import SimpleNamespace

import core_rebalance_score_compat as compat
import runtime_integrity_patch as patch


def test_normalized_score_matches_equivalent_percent_threshold():
    assert compat._score_meets_threshold(0.5376, 52.0) is True
    assert compat._score_meets_threshold(0.5199, 52.0) is False
    assert compat._score_meets_threshold(53.76, 52.0) is True


def test_candidate_keeps_confidence_and_explicit_hold_requirements():
    original = patch._core_rebalance_candidate_allowed
    try:
        compat.install_core_rebalance_score_compat()
        eligible = SimpleNamespace(
            action="HOLD",
            score=0.5376,
            confidence=0.5526,
            portfolio_intent=patch.CORE_REBALANCE_STRATEGIC_CANDIDATE_INTENT,
        )
        low_confidence = SimpleNamespace(
            action="HOLD",
            score=0.90,
            confidence=0.47,
            portfolio_intent=patch.CORE_REBALANCE_CANDIDATE_INTENT,
        )
        non_hold = SimpleNamespace(
            action="SELL",
            score=0.90,
            confidence=0.90,
            portfolio_intent=patch.CORE_REBALANCE_STRATEGIC_CANDIDATE_INTENT,
        )
        plain_hold = SimpleNamespace(action="HOLD", score=0.90, confidence=0.90)

        assert patch._core_rebalance_candidate_allowed(
            eligible,
            market="crypto",
            deployment_gap=1900.0,
            score_threshold=52.0,
            confidence_threshold=0.48,
        ) is True
        assert patch._core_rebalance_candidate_allowed(
            low_confidence,
            market="crypto",
            deployment_gap=1900.0,
            score_threshold=52.0,
            confidence_threshold=0.48,
        ) is False
        assert patch._core_rebalance_candidate_allowed(
            non_hold,
            market="crypto",
            deployment_gap=1900.0,
            score_threshold=52.0,
            confidence_threshold=0.48,
        ) is False
        assert patch._core_rebalance_candidate_allowed(
            plain_hold,
            market="crypto",
            deployment_gap=1900.0,
            score_threshold=52.0,
            confidence_threshold=0.48,
        ) is False
    finally:
        patch._core_rebalance_candidate_allowed = original


def test_no_deployment_gap_still_blocks_candidate():
    original = patch._core_rebalance_candidate_allowed
    try:
        compat.install_core_rebalance_score_compat()
        signal = SimpleNamespace(
            action="HOLD",
            score=0.90,
            confidence=0.90,
            portfolio_intent=patch.CORE_REBALANCE_STRATEGIC_CANDIDATE_INTENT,
        )
        assert patch._core_rebalance_candidate_allowed(
            signal,
            market="crypto",
            deployment_gap=0.0,
            score_threshold=52.0,
            confidence_threshold=0.48,
        ) is False
    finally:
        patch._core_rebalance_candidate_allowed = original
