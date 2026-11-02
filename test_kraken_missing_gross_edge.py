"""Kraken paper entries with cost but no measurable gross edge must fail closed.

These are synthetic, authenticated adapter receipts, not historical fills or
claims of profitable forward performance. Non-Kraken research is unchanged.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import paper_strategy_economics as economics
from paper_strategy_execution_guard import _with_entry_economics_provenance
from test_kraken_paper_pricing import provider


@pytest.mark.parametrize("quant", [None, SimpleNamespace(net_expected_value_pct=0.02)])
def test_valid_kraken_quote_with_missing_gross_edge_blocks_before_cohort_query(monkeypatch, quant):
    pricing, _ = provider(monkeypatch)
    quote = pricing.quote("BTC-USD")
    assert pricing.authentic(quote, "BTC-USD")
    signal = {"symbol": "BTC-USD", "estimated_cost_pct": 0.20}
    enriched = _with_entry_economics_provenance(signal, quote, quant)
    lookup = Mock(side_effect=AssertionError("missing-edge Kraken entry must not query cohorts"))
    monkeypatch.setattr(economics, "strategy_economics", lookup)

    allowed, reason, edge, cost = economics.fee_edge_allows_entry(enriched)
    assert allowed is False
    assert reason == "kraken_paper_gross_edge_unavailable"
    assert edge is None
    assert cost > 1.2
    lookup.assert_not_called()
    assert "paper_pricing_edge_unavailable" not in signal


def test_non_kraken_missing_edge_exploration_is_unchanged(monkeypatch):
    signal = {"symbol": "BTC-USD", "estimated_cost_pct": 0.20}
    enriched = _with_entry_economics_provenance(signal, {"provider": "Yahoo Finance"})
    lookup = Mock(return_value=SimpleNamespace(sample_count=0, expectancy=0.0, profit_factor=0.0))
    monkeypatch.setattr(economics, "strategy_economics", lookup)

    allowed, reason, edge, cost = economics.fee_edge_allows_entry(enriched)
    assert allowed is True
    assert reason == "edge_unavailable_insufficient_evidence_exploration"
    assert edge is None and cost == 0.20
    lookup.assert_called_once()


def test_kraken_with_valid_gross_edge_keeps_existing_cost_threshold(monkeypatch):
    pricing, _ = provider(monkeypatch)
    quote = pricing.quote("BTC-USD")
    signal = {"symbol": "BTC-USD", "expected_edge_pct": 0.10, "estimated_cost_pct": 0.20}
    enriched = _with_entry_economics_provenance(signal, quote)
    monkeypatch.setattr(economics, "strategy_economics",
                        Mock(return_value=SimpleNamespace(sample_count=0, expectancy=0.0, profit_factor=0.0)))
    allowed, reason, edge, cost = economics.fee_edge_allows_entry(enriched)
    assert not allowed and reason.startswith("edge_below_round_trip_cost:")
    assert edge == 0.10 and cost > 1.2
