"""Fallback execution-cost components must never turn invalid estimates into free costs.

Synthetic paper signals only; no orders, persisted fills, or historical changes.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import paper_strategy_economics as economics


@pytest.mark.parametrize("field", [
    "expected_slippage_pct", "slippage_pct", "spread_pct", "fee_pct",
])
@pytest.mark.parametrize("invalid", [
    float("nan"), float("inf"), float("-inf"), -0.25, "invalid-cost",
])
@pytest.mark.parametrize("shape", ["dict", "object"])
@pytest.mark.parametrize("edge", [None, 0.25])
def test_invalid_fallback_component_blocks_before_cohort_lookup(monkeypatch, field, invalid, shape, edge):
    fields = {"symbol": "BTC-USD", field: invalid}
    if edge is not None:
        fields["expected_edge_pct"] = edge
    signal = fields if shape == "dict" else SimpleNamespace(**fields)
    lookup = Mock(side_effect=AssertionError("invalid component must block before cohort lookup"))
    monkeypatch.setattr(economics, "strategy_economics", lookup)

    allowed, reason, observed_edge, cost = economics.fee_edge_allows_entry(signal)

    assert allowed is False
    assert reason == "paper_explicit_cost_invalid"
    assert observed_edge == edge
    assert cost == float("inf")
    lookup.assert_not_called()


@pytest.mark.parametrize("field", [
    "expected_slippage_pct", "slippage_pct", "spread_pct", "fee_pct",
])
@pytest.mark.parametrize("valid", [0.0, 0.1, 0.8])
def test_nonnegative_fallback_component_preserves_cost_math(field, valid):
    signal = {"symbol": "BTC-USD", field: valid}
    cost = economics.estimated_round_trip_cost_pct(signal)
    slippage = valid if field in {"expected_slippage_pct", "slippage_pct"} else 0.165
    spread = valid if field == "spread_pct" else 0.0
    fee = valid if field == "fee_pct" else 0.10
    assert cost == pytest.approx(2 * slippage + spread + 2 * fee)


def test_absent_fallback_components_preserve_defaults():
    assert economics.estimated_round_trip_cost_pct({"symbol": "BTC-USD"}) == pytest.approx(0.53)


def test_explicit_valid_total_cost_still_takes_precedence_over_bad_fallback():
    assert economics.estimated_round_trip_cost_pct({
        "symbol": "BTC-USD", "estimated_cost_pct": 0.75,
        "spread_pct": float("nan"),
    }) == 0.75
