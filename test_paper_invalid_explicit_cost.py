"""Explicit invalid paper cost must never become a free-entry estimate.

Synthetic signals only. This does not alter or infer historical trades.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import paper_strategy_economics as economics


@pytest.mark.parametrize("invalid", [
    float("nan"), float("inf"), float("-inf"), -0.25, "invalid-cost",
])
@pytest.mark.parametrize("shape", ["dict", "object"])
@pytest.mark.parametrize("edge", [None, 0.25])
def test_invalid_explicit_cost_blocks_before_cohort_lookup(monkeypatch, invalid, shape, edge):
    fields = {"symbol": "BTC-USD", "estimated_cost_pct": invalid}
    if edge is not None:
        fields["expected_edge_pct"] = edge
    signal = fields if shape == "dict" else SimpleNamespace(**fields)
    lookup = Mock(side_effect=AssertionError("invalid cost must not query historical cohorts"))
    monkeypatch.setattr(economics, "strategy_economics", lookup)

    allowed, reason, observed_edge, cost = economics.fee_edge_allows_entry(signal)

    assert allowed is False
    assert reason == "paper_explicit_cost_invalid"
    assert observed_edge == edge
    assert cost == float("inf")
    lookup.assert_not_called()


@pytest.mark.parametrize("valid", [0.0, 0.2, 1.25])
def test_valid_explicit_cost_preserves_original_semantics(monkeypatch, valid):
    signal = {"symbol": "BTC-USD", "expected_edge_pct": 5.0, "estimated_cost_pct": valid}
    monkeypatch.setattr(
        economics, "strategy_economics",
        Mock(return_value=SimpleNamespace(sample_count=0, expectancy=0.0, profit_factor=0.0)),
    )
    allowed, reason, edge, cost = economics.fee_edge_allows_entry(signal)
    assert allowed and reason == "edge_clears_round_trip_cost"
    assert edge == 5.0 and cost == valid


def test_absent_explicit_cost_uses_existing_conservative_components():
    cost = economics.estimated_round_trip_cost_pct({"symbol": "BTC-USD"})
    assert cost > 0 and cost != float("inf")
