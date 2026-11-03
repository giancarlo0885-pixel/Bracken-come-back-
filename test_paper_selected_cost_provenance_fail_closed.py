"""Invalid selected optimizer/quant execution costs cannot become fallback paper costs.

Synthetic entry evidence only; no broker, ledger, Brain or AEVE mutation.
"""
import math
from types import SimpleNamespace

import pytest

import paper_strategy_economics as economics
from paper_strategy_execution_guard import _with_entry_economics_provenance


BAD_COSTS = [
    pytest.param(True, id="boolean-true"),
    pytest.param(False, id="boolean-false"),
    pytest.param(-0.01, id="negative"),
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="infinite"),
    pytest.param("unknown", id="nonnumeric"),
    pytest.param("", id="empty"),
]


def _assert_rejected_before_history(monkeypatch, enriched):
    assert enriched.get("paper_pricing_cost_unavailable") is True
    monkeypatch.setattr(
        economics, "strategy_economics",
        lambda _: (_ for _ in ()).throw(
            AssertionError("invalid explicit cost must fail before history lookup")
        ),
    )
    allowed, reason, edge, cost = economics.fee_edge_allows_entry(enriched)
    assert not allowed
    assert reason == "paper_pricing_cost_unavailable"
    assert edge == 5.0
    assert math.isinf(cost)


@pytest.mark.parametrize("invalid_cost", BAD_COSTS)
def test_invalid_optimizer_cost_cannot_be_replaced_by_generic_fallback(
    monkeypatch, invalid_cost
):
    signal = {
        "symbol": "BTC-USD",
        "expected_edge_pct": 5.0,
        "v39_optimizer_allocation": {
            "estimated_round_trip_cost_pct": invalid_cost,
        },
    }
    enriched = _with_entry_economics_provenance(signal, None)
    _assert_rejected_before_history(monkeypatch, enriched)
    assert "paper_pricing_cost_unavailable" not in signal


@pytest.mark.parametrize("invalid_cost", BAD_COSTS)
def test_invalid_quant_cost_cannot_be_replaced_by_generic_fallback(
    monkeypatch, invalid_cost
):
    signal = {"symbol": "BTC-USD", "expected_edge_pct": 5.0}
    quant = SimpleNamespace(estimated_cost_pct=invalid_cost)
    enriched = _with_entry_economics_provenance(signal, None, quant)
    _assert_rejected_before_history(monkeypatch, enriched)
    assert "paper_pricing_cost_unavailable" not in signal


def test_invalid_optimizer_cost_stays_rejected_even_with_valid_quant_cost(monkeypatch):
    signal = {
        "symbol": "BTC-USD",
        "expected_edge_pct": 5.0,
        "v39_optimizer_allocation": {"estimated_round_trip_cost_pct": True},
    }
    enriched = _with_entry_economics_provenance(
        signal, None, SimpleNamespace(estimated_cost_pct=0.002)
    )
    _assert_rejected_before_history(monkeypatch, enriched)


def test_valid_optimizer_cost_still_takes_priority_over_valid_quant_cost():
    signal = {
        "expected_edge_pct": 5.0,
        "v39_optimizer_allocation": {"estimated_round_trip_cost_pct": 0.22},
    }
    enriched = _with_entry_economics_provenance(
        signal, None, SimpleNamespace(estimated_cost_pct=0.004)
    )
    assert economics.estimated_round_trip_cost_pct(enriched) == 0.22
    assert enriched.get("paper_pricing_cost_unavailable") is not True


def test_absent_optimizer_and_quant_costs_keep_existing_fallback():
    signal = {
        "expected_edge_pct": 5.0,
        "v39_optimizer_allocation": {},
    }
    enriched = _with_entry_economics_provenance(signal, None, SimpleNamespace())
    assert enriched is signal
    assert economics.estimated_round_trip_cost_pct(enriched) > 0
