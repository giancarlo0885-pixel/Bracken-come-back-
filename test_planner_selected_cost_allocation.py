"""Selected quote costs govern real optimizer admission before risk and allocation.

The original source wrapper passes the 0.8% case through to the risk gate using
its generic 0.2% estimate. The selected Kraken model's fees alone exceed 1.2%, so
that candidate must be rejected economically before risk or cash reservation.
"""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import global_adaptive_engine as adaptive
import paper_strategy_economics as economics
import strategic_rebalance_optimizer_bridge as bridge
from coinbase_paper_pricing import FEE
from crypto_execution_source import install_crypto_candidate_source_guard
from test_kraken_paper_pricing import provider
from test_kraken_reference_book import NOW
from test_strategic_rebalance_optimizer_bridge import _candidate, _worker


def _plan(monkeypatch, edge, *, stale=False):
    pricing, _ = provider(monkeypatch)
    quote = pricing.quote('BTC-USD')
    assert pricing.authentic(quote, 'BTC-USD')
    candidate = _candidate(tactical_action='BUY', expected_edge_pct=edge,
                           estimated_cost_pct=0.20)
    worker = _worker()
    worker._v39_signal_opportunity = lambda *_: candidate
    install_crypto_candidate_source_guard(worker, pricing.authentic,
                                          'UNVERIFIED_OR_STALE', require_forecast=True)
    if stale:
        pricing.clock = lambda: NOW + timedelta(seconds=6)
    signal = {'symbol': 'BTC-USD', 'signal_id': 123, 'forecast_id': 'original'}
    before_inputs = deepcopy((candidate, quote, signal))
    opportunity = worker._v39_signal_opportunity('crypto', signal, {'BTC-USD': quote}, {}, 'fast')
    assert (candidate, quote, signal) == before_inputs
    monkeypatch.setattr(economics, 'strategy_economics', Mock(return_value=SimpleNamespace(
        sample_count=0, expectancy=0.0, profit_factor=0.0)))
    # A sentinel hard veto demonstrates economics cannot authorize a fill or
    # bypass the ordinary risk gate, even when edge clears the selected costs.
    risk = Mock(return_value={'allowed': False, 'reasons': ['risk sentinel']})
    monkeypatch.setattr(adaptive, 'hard_risk_gate', risk)
    bridge.install_strategic_rebalance_optimizer_bridge(worker)
    portfolio = {'cash': 2000.0, 'equity': 2000.0, 'buying_power': 2000.0}
    before_portfolio = deepcopy(portfolio)
    plan = worker.adaptive_portfolio_optimizer([opportunity], portfolio, [], engine='crypto')
    assert portfolio == before_portfolio and plan['allocations'] == []
    return plan, opportunity, risk


def test_edge_below_selected_model_cost_never_reaches_risk_or_allocation(monkeypatch):
    plan, opportunity, risk = _plan(monkeypatch, 0.8)
    rejection = plan['rejections'][0]
    assert rejection['reason'] == 'economics_blocked'
    assert rejection['economics_reason'].startswith('edge_below_round_trip_cost:')
    assert rejection['expected_edge_pct'] == 0.8
    assert rejection['estimated_round_trip_cost_pct'] >= ((1 + FEE) / (1 - FEE) - 1) * 100
    assert opportunity['paper_cost_floor_quote_id'].startswith('kraken-paper:')
    risk.assert_not_called()


def test_edge_above_selected_cost_still_requires_ordinary_risk_gate(monkeypatch):
    plan, _, risk = _plan(monkeypatch, 3.0)
    assert plan['rejections'][0]['reason'] == 'hard risk gate'
    assert plan['rejections'][0]['risk_reasons'] == ['risk sentinel']
    risk.assert_called_once()


def test_expired_selected_receipt_is_not_capital_qualified(monkeypatch):
    plan, opportunity, risk = _plan(monkeypatch, 3.0, stale=True)
    assert opportunity['execution_source_eligible'] is False
    assert 'paper_cost_floor_quote_id' not in opportunity
    assert plan['rejections'][0]['reason'] == 'not_capital_qualified'
    risk.assert_not_called()
