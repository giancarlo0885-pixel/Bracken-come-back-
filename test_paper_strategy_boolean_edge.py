"""Fail closed on boolean values masquerading as explicit gross-return forecasts.

Synthetic data only; this test does not imply any production occurrence or profit.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import paper_strategy_economics as economics


GROSS_EDGE_FIELDS = (
    "expected_edge_pct",
    "calibrated_expected_edge_pct",
    "expected_return_pct",
    "forecast_return_pct",
    "possible_move_pct",
    "expected_move_pct",
    "edge_pct",
)


@pytest.mark.parametrize("field", GROSS_EDGE_FIELDS)
@pytest.mark.parametrize("invalid", [True, False], ids=["true", "false"])
@pytest.mark.parametrize("as_object", [False, True], ids=["mapping", "object"])
def test_boolean_gross_edge_fails_closed_before_strategy_lookup(
    monkeypatch, field, invalid, as_object
):
    candidate = {"symbol": "BTC-USD", field: invalid, "estimated_cost_pct": 0.20}
    signal = SimpleNamespace(**candidate) if as_object else candidate
    lookup = Mock(side_effect=AssertionError("invalid explicit edge must not query cohorts"))
    monkeypatch.setattr(economics, "strategy_economics", lookup)

    assert economics.expected_edge_pct(signal) is None
    allowed, reason, edge, cost = economics.fee_edge_allows_entry(signal)
    assert allowed is False
    assert reason == "invalid_explicit_gross_edge_type"
    assert edge is None
    assert cost == pytest.approx(0.20)
    lookup.assert_not_called()


def test_boolean_secondary_edge_cannot_be_masked_by_numeric_primary(monkeypatch):
    signal = {
        "symbol": "BTC-USD",
        "expected_edge_pct": 1.0,
        "forecast_return_pct": True,
        "estimated_cost_pct": 0.20,
    }
    lookup = Mock(side_effect=AssertionError("conflicting explicit evidence must fail closed"))
    monkeypatch.setattr(economics, "strategy_economics", lookup)
    allowed, reason, _, _ = economics.fee_edge_allows_entry(signal)
    assert not allowed and reason == "invalid_explicit_gross_edge_type"
    lookup.assert_not_called()


def test_numeric_gross_edge_preserves_existing_post_cost_gate(monkeypatch):
    signal = {"symbol": "BTC-USD", "expected_edge_pct": 1.0, "estimated_cost_pct": 0.20}
    lookup = Mock(return_value=SimpleNamespace(
        sample_count=0, expectancy=0.0, profit_factor=0.0,
    ))
    monkeypatch.setattr(economics, "strategy_economics", lookup)
    assert economics.expected_edge_pct(signal) == pytest.approx(1.0)
    allowed, reason, edge, cost = economics.fee_edge_allows_entry(signal)
    assert allowed and reason == "edge_clears_round_trip_cost"
    assert edge == pytest.approx(1.0) and cost == pytest.approx(0.20)
    lookup.assert_called_once()
