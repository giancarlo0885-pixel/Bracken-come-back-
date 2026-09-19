from types import SimpleNamespace

import paper_strategy_economics as economics
from paper_strategy_execution_guard import _with_entry_economics_provenance


def test_verified_spread_is_used_when_signal_has_none():
    signal = {"action": "BUY"}
    enriched = _with_entry_economics_provenance(signal, {"spread_pct": 0.731098})
    assert "spread_pct" not in signal
    assert enriched["spread_pct"] == 0.731098
    cost = economics.estimated_round_trip_cost_pct(enriched)
    assert cost >= 0.731098
    assert abs(cost - 1.261098) < 1e-9


def test_existing_entry_spread_is_immutable():
    signal = {"spread_pct": 0.12}
    enriched = _with_entry_economics_provenance(signal, {"spread_pct": 0.73})
    assert enriched is signal
    assert economics.estimated_round_trip_cost_pct(enriched) >= 0.12


def test_negative_edge_sign_is_preserved():
    assert economics.expected_edge_pct({"expected_return_pct": -0.25}) == -0.25


def test_object_signal_is_copied_not_mutated():
    signal = SimpleNamespace(action="BUY")
    enriched = _with_entry_economics_provenance(signal, {"spread_pct": 0.40})
    assert not hasattr(signal, "spread_pct")
    assert enriched.spread_pct == 0.40


def test_missing_or_invalid_verified_spread_does_not_invent_cost_data():
    signal = {"action": "BUY"}
    assert _with_entry_economics_provenance(signal, None) is signal
    assert _with_entry_economics_provenance(signal, {"spread_pct": -1}) is signal
