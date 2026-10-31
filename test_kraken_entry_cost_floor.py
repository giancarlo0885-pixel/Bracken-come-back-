"""Final entry economics must cover the actual Kraken paper pricing model."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import paper_strategy_economics as economics
from paper_strategy_execution_guard import _with_entry_economics_provenance
from paper_execution_accounting import _simulate_fill_explicit_fee
from test_kraken_paper_pricing import provider


def minimum_roundtrip(q):
    buy=_simulate_fill_explicit_fee(side='BUY',market='crypto',reference_price=q['price'],quote=q,market_impact_pct=0)
    sell=_simulate_fill_explicit_fee(side='SELL',market='crypto',reference_price=q['price'],quote=q,market_impact_pct=0)
    return (buy.fill_price*(1+buy.fee_pct)/(sell.fill_price*(1-sell.fee_pct))-1)*100


@pytest.mark.parametrize('origin',['optimizer','quant','signal','none'])
def test_final_cost_cannot_be_below_actual_paper_roundtrip(monkeypatch,origin):
    p,_=provider(monkeypatch);q=p.quote('BTC-USD');original_q=deepcopy(q)
    signal={'symbol':'BTC-USD','expected_edge_pct':.8}
    quant=None
    if origin=='optimizer':signal['v39_optimizer_allocation']={'estimated_round_trip_cost_pct':.2}
    elif origin=='quant':quant=SimpleNamespace(estimated_cost_pct=.002)
    elif origin=='signal':signal['estimated_cost_pct']=.2
    original=deepcopy(signal)
    enriched=_with_entry_economics_provenance(signal,q,quant)
    assert economics.estimated_round_trip_cost_pct(enriched)>=minimum_roundtrip(q)-1e-12
    assert signal==original and q==original_q and p.authentic(q,'BTC-USD')
    # An optimistic gross edge cannot clear the real modeled trading friction.
    monkeypatch.setattr(economics,'strategy_economics',Mock(return_value=SimpleNamespace(sample_count=0,expectancy=0,profit_factor=0)))
    assert economics.fee_edge_allows_entry(enriched)[0] is False


def test_more_conservative_existing_cost_is_not_added_again(monkeypatch):
    p,_=provider(monkeypatch);q=p.quote('BTC-USD')
    enriched=_with_entry_economics_provenance({'symbol':'BTC-USD','estimated_cost_pct':5.0},q)
    assert economics.estimated_round_trip_cost_pct(enriched)==5.0


def test_object_signal_and_spread_units_preserve_original(monkeypatch):
    p,_=provider(monkeypatch);q=p.quote('BTC-USD')
    signal=SimpleNamespace(symbol='BTC-USD',expected_edge_pct=.8)
    enriched=_with_entry_economics_provenance(signal,q)
    assert enriched.spread_pct==pytest.approx(q['spread_pct']*100)
    assert economics.estimated_round_trip_cost_pct(enriched)>=minimum_roundtrip(q)-1e-12
    assert not hasattr(signal,'estimated_cost_pct')


def test_generic_quotes_and_existing_non_kraken_semantics_unchanged():
    signal={'estimated_cost_pct':.2,'symbol':'BTC-USD'}
    assert _with_entry_economics_provenance(signal,{'provider':'Yahoo Finance'}) is signal


@pytest.mark.parametrize('patch',[{'symbol':'ETH-USD'},{'paper_fee_pct':None},
    {'paper_price_model':'unknown'},{'execution_authority':'NONE'}])
@pytest.mark.parametrize('edge',[None,100.0])
def test_ambiguous_selected_pricing_cost_blocks_even_missing_edge_exploration(monkeypatch,patch,edge):
    p,_=provider(monkeypatch);q=p.quote('BTC-USD');q.update(patch)
    signal={'symbol':'BTC-USD','estimated_cost_pct':.2}
    if edge is not None:signal['expected_edge_pct']=edge
    enriched=_with_entry_economics_provenance(signal,q)
    lookup=Mock(side_effect=AssertionError('unknown costs must block before historical exploration'))
    monkeypatch.setattr(economics,'strategy_economics',lookup)
    allowed,reason,_,cost=economics.fee_edge_allows_entry(enriched)
    assert not allowed and reason=='paper_pricing_cost_unavailable'
    assert cost==float('inf')
    lookup.assert_not_called()
