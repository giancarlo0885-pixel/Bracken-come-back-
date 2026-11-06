"""Boolean-valued forecast fields must never become paper trading edge evidence.

Synthetic research-only fixtures; no real fills or strategy promotion.
"""
from types import SimpleNamespace

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
@pytest.mark.parametrize("boolean", [True, False])
def test_boolean_forecast_is_not_a_numeric_edge_or_exploration_permission(
    monkeypatch, field, boolean
):
    monkeypatch.setattr(
        economics, "strategy_economics",
        lambda _: (_ for _ in ()).throw(
            AssertionError("invalid explicit evidence must fail before history lookup")
        ),
    )
    signal = {field: boolean}
    assert economics.expected_edge_pct(signal) is None
    allowed, reason, edge, cost = economics.fee_edge_allows_entry(signal)
    assert allowed is False
    assert reason == "invalid_explicit_forecast_edge"
    assert edge is None
    assert cost > 0


def test_boolean_edge_on_object_signal_is_rejected_without_fallback(monkeypatch):
    monkeypatch.setattr(
        economics, "strategy_economics",
        lambda _: (_ for _ in ()).throw(AssertionError("unexpected economics lookup")),
    )
    signal = SimpleNamespace(expected_return_pct=True)
    assert economics.expected_edge_pct(signal) is None
    allowed, reason, edge, cost = economics.fee_edge_allows_entry(signal)
    assert (allowed, reason, edge) == (False, "invalid_explicit_forecast_edge", None)
    assert cost > 0


def test_mixed_boolean_and_numeric_forecast_fails_closed(monkeypatch):
    monkeypatch.setattr(
        economics, "strategy_economics",
        lambda _: (_ for _ in ()).throw(AssertionError("unexpected economics lookup")),
    )
    signal = {"expected_edge_pct": 2.0, "forecast_return_pct": True}
    allowed, reason, edge, cost = economics.fee_edge_allows_entry(signal)
    assert not allowed
    assert reason == "invalid_explicit_forecast_edge"
    assert edge == 2.0
    assert cost > 0


def test_numeric_forecast_and_missing_edge_keep_existing_semantics(monkeypatch):
    monkeypatch.setattr(
        economics,
        "strategy_economics",
        lambda _: SimpleNamespace(sample_count=0, expectancy=0.0, profit_factor=0.0),
    )
    allowed, reason, edge, cost = economics.fee_edge_allows_entry(
        {"expected_edge_pct": "2.0", "estimated_cost_pct": 0.5}
    )
    assert allowed and reason == "edge_clears_round_trip_cost"
    assert edge == 2.0 and cost == 0.5
    allowed, reason, edge, cost = economics.fee_edge_allows_entry({})
    assert allowed and reason == "edge_unavailable_insufficient_evidence_exploration"
    assert edge is None and cost > 0
