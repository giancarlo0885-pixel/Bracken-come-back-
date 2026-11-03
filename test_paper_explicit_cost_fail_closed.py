"""Malformed explicit paper costs must not become zero-cost opportunities.

Research-only regression for the shared planner/final fee-edge calculation.
"""
import math
from types import SimpleNamespace

import pytest

import paper_strategy_economics as economics


@pytest.mark.parametrize(
    "malformed",
    [
        pytest.param(-0.01, id="negative"),
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="infinite"),
        pytest.param("not-a-cost", id="nonnumeric"),
        pytest.param("", id="empty"),
        pytest.param(True, id="boolean-true"),
        pytest.param(False, id="boolean-false"),
    ],
)
def test_invalid_explicit_paper_cost_fails_closed(monkeypatch, malformed):
    # High claimed gross edge must not rehabilitate invalid cost evidence.
    signal = {"symbol": "BTC-USD", "expected_edge_pct": 20.0,
              "estimated_cost_pct": malformed}
    monkeypatch.setattr(
        economics, "strategy_economics",
        lambda _: SimpleNamespace(sample_count=0, expectancy=0.0, profit_factor=0.0),
    )
    assert math.isinf(economics.estimated_round_trip_cost_pct(signal))
    allowed, reason, edge, cost = economics.fee_edge_allows_entry(signal)
    assert allowed is False
    assert reason == "paper_pricing_cost_unavailable"
    assert edge == 20.0
    assert math.isinf(cost)


@pytest.mark.parametrize("explicit", [0, 0.25, "0.25"])
def test_valid_explicit_cost_preserved(explicit):
    signal = {"estimated_cost_pct": explicit}
    assert economics.estimated_round_trip_cost_pct(signal) == pytest.approx(float(explicit))


def test_missing_explicit_cost_retains_existing_component_fallback(monkeypatch):
    monkeypatch.setenv("PAPER_ESTIMATED_FEE_PCT", "0.10")
    signal = {"expected_slippage_pct": 0.165, "spread_pct": 0.02}
    assert economics.estimated_round_trip_cost_pct(signal) == pytest.approx(0.55)
