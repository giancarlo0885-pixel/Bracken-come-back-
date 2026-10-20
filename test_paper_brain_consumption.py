from datetime import datetime
from unittest.mock import Mock

import pytest

import paper_brain_consumption as provenance


@pytest.mark.parametrize('key,value',[('EXECUTION_MODE','live'),('ENABLE_BROKER_SUBMISSION','true'),
    ('LIVE_TRADING_ARMED','true')])
def test_live_or_broker_arming_never_installs_receipt_runtime(monkeypatch,key,value):
    monkeypatch.setattr(provenance,'_INSTALLED',False)
    for k,v in {'EXECUTION_MODE':'paper','ENABLE_BROKER_SUBMISSION':'false','LIVE_TRADING_ARMED':'false'}.items():monkeypatch.setenv(k,v)
    monkeypatch.setenv(key,value)
    factory=Mock()
    assert not provenance.install_paper_brain_consumption(connect_factory=factory)
    factory.assert_not_called()


@pytest.mark.parametrize('value',[float('nan'),float('inf'),datetime(2026,1,1),object(),{'x':'a'*1048576}])
def test_unrepresentable_inputs_are_not_silently_relabelled_or_truncated(value):
    with pytest.raises((ValueError,TypeError)):provenance.canonical(value)


def test_canonical_inputs_preserve_boolean_and_number_distinctions():
    assert provenance.digest(provenance.canonical({'x':True}))!=provenance.digest(provenance.canonical({'x':1}))
    assert provenance.canonical({'b':2,'a':1})==provenance.canonical({'a':1,'b':2})


def test_missing_decision_evidence_skips_candidate_without_creating_trade(monkeypatch):
    import opportunity_engine
    monkeypatch.setattr(opportunity_engine,'evaluate_opportunity',Mock(side_effect=provenance.DecisionEvidenceUnavailable('missing')))
    assert opportunity_engine.rank_opportunities([{'symbol':'BTC-USD','signal_id':1}],market='crypto')==[]


def test_fill_result_retains_actual_cost_policy_without_later_environment_reconstruction(monkeypatch):
    from paper_execution_accounting import _simulate_fill_explicit_fee
    monkeypatch.setenv('PAPER_MAX_ADVERSE_FILL_PCT','0.02')
    fill=_simulate_fill_explicit_fee(side='BUY',market='crypto',reference_price=100,
        quote={'bid':99,'ask':100},slippage_pct=.03,fee_pct=.006,latency_pct=0,market_impact_pct=0)
    monkeypatch.setenv('PAPER_MAX_ADVERSE_FILL_PCT','0.0001')
    assert fill.max_adverse_fill_pct==.02
    assert fill.fee_excluded_from_fill_price is True
    assert fill.fill_price==102
