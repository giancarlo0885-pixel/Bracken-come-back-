"""Planner capability checks must match the installed adapter's final guard."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import global_adaptive_engine as adaptive
import strategic_rebalance_optimizer_bridge as bridge
import paper_core_rebalance_qualification as qualification
import runtime_integrity_patch as patch
from crypto_execution_source import install_crypto_candidate_source_guard
from test_kraken_paper_pricing import provider,install


def configure(monkeypatch,unbounded):
    monkeypatch.setenv('PAPER_AUTONOMOUS_LEARNING','true')
    monkeypatch.setenv('PAPER_UNBOUNDED_LEARNING','true' if unbounded else 'false')
    for name in ('GLOBAL_PIT_RESERVE_PCT','GLOBAL_PIT_TARGET_INVESTED_PCT','GLOBAL_PIT_MAX_POSITION_PCT','MAX_SECTOR_EXPOSURE_PCT'):
        monkeypatch.setattr(adaptive,name,getattr(adaptive,name))
    monkeypatch.setattr(adaptive,'hard_risk_gate',adaptive.hard_risk_gate)
    monkeypatch.setattr(bridge,'_adaptive_meaningful_entry_floor',bridge._adaptive_meaningful_entry_floor)


def worker(native=False):
    return SimpleNamespace(log=Mock(),_execution_quote_eligible=lambda *_:True,
        _v39_signal_opportunity=lambda *_:{'symbol':'BTC-USD','qualified_for_capital':native,
            'tradeable':True,'avg_dollar_volume':100000,'spread_pct':.01,'risk_score':10,'stages':[]})


@pytest.mark.parametrize('unbounded',[False,True])
@pytest.mark.parametrize('source_first',[False,True])
@pytest.mark.parametrize('native',[False,True])
def test_missing_forecast_cannot_be_promoted_by_learning_or_wrapper_order(monkeypatch,unbounded,source_first,native):
    p,_=provider(monkeypatch);configure(monkeypatch,unbounded);w=worker(native)
    q=p.quote('BTC-USD');before=deepcopy(q)
    signal=SimpleNamespace(symbol='BTC-USD',action='BUY',signal_id=123,forecast_id=None,
        portfolio_intent=patch.CORE_REBALANCE_CANDIDATE_INTENT)
    def source():install_crypto_candidate_source_guard(w,p.authentic,'PAPER_PRICE_UNVERIFIED_OR_STALE',require_forecast=True)
    if source_first:source()
    qualification.install_paper_core_rebalance_qualification(w)
    if not source_first:source()
    result=w._v39_signal_opportunity('crypto',signal,{'BTC-USD':q},{},'fast')
    assert result['qualified_for_capital'] is False
    assert result['entry_evidence_rejection']=='ENTRY_FORECAST_LINK_REQUIRED'
    assert result['execution_source_eligible'] is True  # Valid quote, missing decision evidence.
    assert result['entry_evidence_eligible'] is False
    assert q==before and signal.forecast_id is None
    assert not w.log.info.called  # Do not log forecast-optional success first.


@pytest.mark.parametrize('market',['crypto','cash'])
def test_valid_existing_forecast_or_cash_path_keeps_original_qualification(monkeypatch,market):
    p,_=provider(monkeypatch);w=worker(True)
    install_crypto_candidate_source_guard(w,p.authentic,'UNAVAILABLE',require_forecast=True)
    signal={'symbol':'BTC-USD','signal_id':123,'forecast_id':'original'}
    q=p.quote('BTC-USD')
    out=w._v39_signal_opportunity(market,signal,{'BTC-USD':q},{},'fast')
    assert out['qualified_for_capital'] is True and q['provider_quote_verified'] is False


def test_real_adapter_installer_advertises_actual_forecast_requirement(monkeypatch):
    p,q,w,_,_=install(monkeypatch)
    assert w._crypto_entry_requires_forecast is True
    assert w._v39_signal_opportunity('crypto',{'symbol':'BTC-USD','signal_id':1},{'BTC-USD':q},{},'fast')['qualified_for_capital'] is False
    assert p.authentic(q,'BTC-USD')


def test_legacy_source_contract_is_not_changed_without_declared_requirement():
    w=worker(True)
    install_crypto_candidate_source_guard(w,lambda *_:True,'UNAVAILABLE')
    assert w._v39_signal_opportunity('crypto',{'symbol':'BTC-USD'},{},{},'fast')['qualified_for_capital'] is True


def test_actual_installed_adapter_planner_agrees_with_final_forecast_veto(monkeypatch):
    import oracle_bot
    p,q,w,_,_=install(monkeypatch)
    signal={'symbol':'BTC-USD','signal_id':123,'forecast_id':None}
    assert oracle_bot._execution_quote_guard('crypto','BTC-USD',q['price'],signal,quote_metadata=q)==(False,'ENTRY_FORECAST_LINK_REQUIRED')
    plan=w._v39_signal_opportunity('crypto',signal,{'BTC-USD':q},{},'fast')
    assert plan['qualified_for_capital'] is False


def test_rejected_missing_forecast_gets_no_optimizer_allocation_or_cash_reservation(monkeypatch):
    p,_=provider(monkeypatch);w=worker(True)
    install_crypto_candidate_source_guard(w,p.authentic,'UNAVAILABLE',require_forecast=True)
    signal={'symbol':'BTC-USD','signal_id':123,'forecast_id':None}
    q=p.quote('BTC-USD')
    opportunity=w._v39_signal_opportunity('crypto',signal,{'BTC-USD':q},{},'fast')
    opportunity.update(asset_class='crypto',core_rebalance_candidate=True,
        portfolio_intent=patch.CORE_REBALANCE_CANDIDATE_INTENT,tactical_action='BUY')
    w.adaptive_portfolio_optimizer=adaptive.adaptive_portfolio_optimizer
    bridge.install_strategic_rebalance_optimizer_bridge(w)
    portfolio={'cash':2000.,'equity':2000.,'buying_power':2000.};before=dict(portfolio)
    plan=w.adaptive_portfolio_optimizer([opportunity],portfolio,[],engine='crypto')
    assert plan['allocations']==[] and portfolio==before
    assert any(r['reason']=='not_capital_qualified' for r in plan['rejections'])
