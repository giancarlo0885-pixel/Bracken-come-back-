from types import SimpleNamespace

import paper_strategy_economics as economics
from paper_strategy_execution_guard import _with_entry_economics_provenance


def quant(*, gross=None, cost=None, net=None):
    values = {}
    if gross is not None:
        values["gross_expected_value_pct"] = gross
    if cost is not None:
        values["estimated_cost_pct"] = cost
    if net is not None:
        values["net_expected_value_pct"] = net
    return SimpleNamespace(**values)


def test_quant_gross_edge_and_cost_convert_to_percentage_points():
    source = {"action": "BUY"}
    enriched = _with_entry_economics_provenance(
        source,
        None,
        quant(gross=0.0150, cost=0.0025, net=0.0125),
    )
    assert source == {"action": "BUY"}
    assert economics.expected_edge_pct(enriched) == 1.5
    assert economics.estimated_round_trip_cost_pct(enriched) == 0.25
    assert "net_expected_value_pct" not in enriched


def test_quant_net_plus_exact_cost_reconstructs_gross_edge_without_double_counting():
    enriched = _with_entry_economics_provenance(
        {},
        None,
        quant(cost=0.0025, net=0.0125),
    )
    assert abs(economics.expected_edge_pct(enriched) - 1.5) < 1e-12
    assert economics.estimated_round_trip_cost_pct(enriched) == 0.25


def test_quant_net_without_exact_cost_does_not_invent_gross_edge():
    enriched = _with_entry_economics_provenance({}, None, quant(net=0.0125))
    assert economics.expected_edge_pct(enriched) is None


def test_negative_quant_gross_edge_sign_is_preserved():
    enriched = _with_entry_economics_provenance(
        {},
        None,
        quant(gross=-0.004, cost=0.001, net=-0.005),
    )
    assert economics.expected_edge_pct(enriched) == -0.4
    assert economics.estimated_round_trip_cost_pct(enriched) == 0.1


def test_originating_signal_edge_wins_over_quant_fallback_but_quant_cost_is_preserved():
    source = {"expected_return_pct": -0.25}
    enriched = _with_entry_economics_provenance(
        source,
        None,
        quant(gross=0.02, cost=0.001, net=0.019),
    )
    assert source == {"expected_return_pct": -0.25}
    assert economics.expected_edge_pct(enriched) == -0.25
    assert economics.estimated_round_trip_cost_pct(enriched) == 0.1


def test_optimizer_cost_provenance_wins_over_quant_cost():
    source = {
        "expected_return_pct": 1.0,
        "v39_optimizer_allocation": {
            "estimated_round_trip_cost_pct": 0.22,
        },
    }
    enriched = _with_entry_economics_provenance(
        source,
        None,
        quant(gross=0.02, cost=0.004, net=0.016),
    )
    assert economics.expected_edge_pct(enriched) == 1.0
    assert economics.estimated_round_trip_cost_pct(enriched) == 0.22


def test_quant_edge_and_verified_spread_are_combined_without_mutating_source():
    source = {"action": "BUY"}
    enriched = _with_entry_economics_provenance(
        source,
        {"spread_pct": 0.30},
        quant(gross=0.012, cost=0.002, net=0.010),
    )
    assert source == {"action": "BUY"}
    assert economics.expected_edge_pct(enriched) == 1.2
    assert economics.estimated_round_trip_cost_pct(enriched) == 0.2
    assert enriched["spread_pct"] == 0.30


def test_missing_quant_economics_does_not_invent_edge():
    source = {"action": "BUY"}
    enriched = _with_entry_economics_provenance(source, None, SimpleNamespace())
    assert enriched is source
    assert economics.expected_edge_pct(enriched) is None
