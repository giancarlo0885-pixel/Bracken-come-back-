from types import SimpleNamespace

import paper_strategy_economics as economics
from paper_strategy_execution_guard import _with_entry_economics_provenance


def quant(edge):
    return SimpleNamespace(net_expected_value_pct=edge)


def test_quant_decimal_edge_converts_to_percentage_points():
    source = {"action": "BUY"}
    enriched = _with_entry_economics_provenance(source, None, quant(0.0125))
    assert "net_expected_value_pct" not in source
    assert economics.expected_edge_pct(enriched) == 1.25


def test_negative_quant_edge_sign_is_preserved():
    enriched = _with_entry_economics_provenance({}, None, quant(-0.004))
    assert economics.expected_edge_pct(enriched) == -0.4


def test_originating_signal_edge_wins_over_quant_fallback():
    source = {"expected_return_pct": -0.25}
    enriched = _with_entry_economics_provenance(source, None, quant(0.02))
    assert enriched is source
    assert economics.expected_edge_pct(enriched) == -0.25


def test_quant_edge_and_verified_spread_are_combined_without_mutating_source():
    source = {"action": "BUY"}
    enriched = _with_entry_economics_provenance(source, {"spread_pct": 0.30}, quant(0.01))
    assert source == {"action": "BUY"}
    assert economics.expected_edge_pct(enriched) == 1.0
    assert enriched["spread_pct"] == 0.30


def test_missing_quant_edge_does_not_invent_edge():
    source = {"action": "BUY"}
    enriched = _with_entry_economics_provenance(source, None, SimpleNamespace())
    assert enriched is source
    assert economics.expected_edge_pct(enriched) is None
