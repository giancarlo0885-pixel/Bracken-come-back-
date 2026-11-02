"""Candidate-isolation regressions for mature losing paper research confirmation.

Synthetic observations only; no broker, database, or canonical accounting access.
"""
from types import SimpleNamespace

import pytest

import paper_strategy_execution_guard as guard


LOSING_RESEARCH = SimpleNamespace(
    model_tier="RESEARCH",
    sample_count=100,
    expectancy=-0.21,
    profit_factor=0.19,
)


def _confirmation(signal, timestamp):
    return guard._research_entry_confirmation_allows(
        "AAVE-USD",
        signal,
        {"quote_timestamp": timestamp},
        0.90,
        0.55,
        LOSING_RESEARCH,
    )


@pytest.mark.parametrize(
    "first,second",
    [
        (
            {"symbol": "AAVE-USD", "action": "BUY", "strategy_name": "momentum_alpha", "model": "m1", "model_version": "v1"},
            {"symbol": "AAVE-USD", "action": "BUY", "strategy_name": "mean_reversion_beta", "model": "m1", "model_version": "v1"},
        ),
        (
            {"symbol": "AAVE-USD", "action": "BUY", "strategy_name": "momentum_alpha", "model": "m1", "model_version": "v1"},
            {"symbol": "AAVE-USD", "action": "BUY", "strategy_name": "momentum_alpha", "model": "m1", "model_version": "v2"},
        ),
    ],
    ids=["different_strategy", "different_model_version"],
)
def test_second_scan_must_match_first_candidate_identity(first, second):
    guard._RESEARCH_ENTRY_CONFIRMATIONS.clear()
    try:
        assert _confirmation(first, "2026-11-02T06:00:00Z") == (
            False, "research_entry_confirmation_pending:1/2"
        )
        # A second quote for a different candidate is not its second confirmation.
        assert _confirmation(second, "2026-11-02T06:01:00Z") == (
            False, "research_entry_confirmation_pending:1/2"
        )
        # The same candidate with a new quote can still complete the two-scan gate.
        assert _confirmation(second, "2026-11-02T06:02:00Z") == (
            True, "research_entry_confirmation_passed:2/2"
        )
    finally:
        guard._RESEARCH_ENTRY_CONFIRMATIONS.clear()
