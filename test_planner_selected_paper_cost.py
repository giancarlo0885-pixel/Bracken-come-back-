"""A validated source candidate must carry the same cost floor into planning."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import paper_strategy_economics as economics
import strategic_rebalance_optimizer_bridge as bridge
from crypto_execution_source import install_crypto_candidate_source_guard
from test_kraken_paper_pricing import provider
from test_kraken_entry_cost_floor import minimum_roundtrip

@pytest.mark.parametrize('prior', [.2, 5.0])
def test_selected_kraken_cost_reaches_optimizer_without_mutation(monkeypatch, prior):
    p,_ = provider(monkeypatch)
    q = p.quote('BTC-USD')
    item = {'symbol':'BTC-USD','qualified_for_capital':True,'expected_edge_pct':.8,
            'estimated_cost_pct':prior,'avg_dollar_volume':1e9}
    w = SimpleNamespace(_v39_signal_opportunity=lambda *_:item)
    install_crypto_candidate_source_guard(w,p.authentic,'UNAVAILABLE',require_forecast=True)
    signal = {'symbol':'BTC-USD','signal_id':123,'forecast_id':'original'}
    before = deepcopy((item,q,signal))
    result = w._v39_signal_opportunity('crypto',signal,{'BTC-USD':q},{},'fast')
    planned = bridge._with_liquidity_cost_provenance(result,10)
    assert economics.estimated_round_trip_cost_pct(planned) == pytest.approx(max(prior,minimum_roundtrip(q)))
    assert planned['paper_cost_floor_quote_id'] == q['quote_id']
    assert (item,q,signal) == before
    assert planned['expected_edge_pct'] == .8


def test_invalid_source_does_not_supply_cost_authority(monkeypatch):
    p,_=provider(monkeypatch)
    q=p.quote('BTC-USD')
    w=SimpleNamespace(_v39_signal_opportunity=lambda *_:{'symbol':'BTC-USD','qualified_for_capital':True})
    install_crypto_candidate_source_guard(w,lambda *_:False,'UNAVAILABLE',require_forecast=True)
    result=w._v39_signal_opportunity('crypto',{'symbol':'BTC-USD'}, {'BTC-USD':q},{},'fast')
    assert not result['qualified_for_capital']
    assert 'paper_cost_floor_pct' not in result
