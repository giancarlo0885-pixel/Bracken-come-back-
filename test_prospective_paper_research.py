"""Synthetic prospective observations, not live connectivity or profitable trades."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, localcontext
from unittest.mock import Mock
import json
import pytest

import prospective_paper_research as pilot
from kraken_paper_pricing import validate_kraken_book
from kraken_reference_book import ReferenceBooks
from test_kraken_reference_book import NOW, ack, message
from test_kraken_paper_pricing import provider, rules


def result():
    return {'symbol':'BTC-USD','status':'ABSTAINED','reason':'causal_model_abstention',
            'forecast':None,'data_role':'KRAKEN_PRIMARY','execution_authority':'NONE',
            'history_fingerprint':'original-closed-history',
            'model_identity':{'model':'causal','model_version':'original'}}


def trial(monkeypatch):
    p,_=provider(monkeypatch)
    return pilot.capture_trial(result(),p,NOW)


def exit_book(at,bid='103.00',ask='103.10',symbol='BTC-USD'):
    s=ReferenceBooks({symbol.replace('-','/'):symbol});s.opened(at)
    s.ingest(ack(symbol.replace('-','/')),at)
    s.ingest(message({bid:'5.00000000'},{ask:'5.00000000'},symbol=symbol.replace('-','/'),when=at),at)
    return validate_kraken_book(symbol,s.health(at)['quotes'][symbol],rules(symbol),at.isoformat(),at)


def test_original_abstention_and_entry_frozen_before_later_exit(monkeypatch):
    t=trial(monkeypatch);original=deepcopy(t)
    assert t['decision_status']=='ABSTAINED' and t['original_forecast'] is None
    assert t['execution_authority']=='NONE' and t['canonical_trade'] is False
    assert pilot.evaluate_trial(t,None,300,NOW+timedelta(seconds=299)) is None
    at=NOW+timedelta(seconds=300)
    out=pilot.evaluate_trial(t,exit_book(at),300,at)
    assert out['status']=='COMPLETED_SIMULATION' and t==original
    with localcontext() as ctx:
        ctx.prec=80
        quantity=Decimal(t['quantity']);proceeds=quantity*Decimal(out['exit_fill_price'])
        assert Decimal(out['net_pnl'])==proceeds-Decimal('25')-Decimal('.15')-proceeds*Decimal('.006')
        assert out['entry_fee']==t['entry_fee']
    assert out['not_realized_trading_profit'] and out['fee_provenance']=='MODELED_NOT_ACCOUNT_VERIFIED'


def test_gross_gain_can_be_net_loss_and_losing_observations_are_retained(monkeypatch):
    t=trial(monkeypatch);at=NOW+timedelta(seconds=300)
    out=pilot.evaluate_trial(t,exit_book(at,'101.00','101.10'),300,at)
    assert Decimal(out['gross_pnl'])>0>Decimal(out['net_pnl'])
    loss=pilot.evaluate_trial(t,exit_book(at,'95.00','95.10'),300,at)
    assert Decimal(loss['gross_pnl'])<0 and Decimal(loss['net_pnl'])<0


@pytest.mark.parametrize('seconds',[361,901+60])
def test_missing_exit_window_never_backfills(monkeypatch,seconds):
    t=trial(monkeypatch);horizon=300 if seconds==361 else 900
    out=pilot.evaluate_trial(t,None,horizon,NOW+timedelta(seconds=seconds))
    assert out['status']=='UNAVAILABLE' and 'net_pnl' not in out
    assert out['reason']=='MISSED_PREDECLARED_EXIT_WINDOW'


def test_exit_requires_actual_later_fresh_same_symbol_book(monkeypatch):
    t=trial(monkeypatch);at=NOW+timedelta(seconds=300)
    assert pilot.evaluate_trial(t,exit_book(at-timedelta(seconds=1)),300,at) is None
    for q in [t['entry_quote'],exit_book(at,symbol='ETH-USD'),{**exit_book(at),'quote_id':'altered'}]:
        with pytest.raises(ValueError):pilot.evaluate_trial(t,q,300,at)
    with pytest.raises(ValueError):pilot.evaluate_trial(t,exit_book(at),600,at)


@pytest.mark.parametrize('field,value',[('quantity','1'),('entry_fee','0'),('execution_authority','PAPER'),
    ('canonical_trade',True),('decision_status','BUY'),('policy_hash','changed')])
def test_entry_tampering_cannot_create_outcomes(monkeypatch,field,value):
    t=trial(monkeypatch);t[field]=value
    with pytest.raises(ValueError):pilot.validate_trial(t)


def test_fresh_forecast_identity_required_and_never_invented(monkeypatch):
    p,_=provider(monkeypatch);r=result();r.update(status='SHADOW_FORECAST',reason=None)
    forecast={'forecast_id':'original','symbol':'BTC-USD','validation_status':'shadow',
        'model_version':'original','generated_at':NOW.isoformat(),'source_quote_timestamp':NOW.isoformat()}
    r['forecast']=forecast
    assert pilot.capture_trial(r,p,NOW)['original_forecast']==forecast
    for patch in [{'generated_at':(NOW+timedelta(seconds=1)).isoformat()},
                  {'generated_at':(NOW-timedelta(seconds=31)).isoformat()},
                  {'symbol':'ETH-USD'},{'forecast_id':None},{'validation_status':'approved'}]:
        with pytest.raises(ValueError):pilot.capture_trial({**r,'forecast':{**forecast,**patch}},p,NOW)


@pytest.mark.parametrize('key,value',[('KRAKEN_FORWARD_PAPER_PILOT_ENABLED','false'),
    ('EXECUTION_MODE','live'),('ENABLE_BROKER_SUBMISSION','true'),('LIVE_TRADING_ARMED','true'),
    ('CRYPTO_PAPER_PRICING_PROVIDER','coinbase')])
def test_explicit_opt_in_does_not_change_trading_authority(monkeypatch,key,value):
    provider(monkeypatch);monkeypatch.setenv('KRAKEN_FORWARD_PAPER_PILOT_ENABLED','true')
    assert pilot.enabled()
    monkeypatch.setenv(key,value);assert not pilot.enabled()


def test_day_proxy_not_independence_and_horizons_not_double_counted(monkeypatch):
    t=trial(monkeypatch);values=[]
    for h in (300,900):
        at=NOW+timedelta(seconds=h)
        out=pilot.evaluate_trial(t,exit_book(at),h,at)
        values.append({'metadata':{**out,'source_evidence_id':1}})
    conn=Mock();conn.execute.return_value.fetchall.return_value=values
    summary=pilot.summarize(conn,{'first_source_id':1,'last_source_id':100})
    assert summary['distinct_entry_observations']==1 and summary['matched_simulated_entries']==1
    assert summary['independent_market_episodes'] is None and summary['canonical_shadow_exit_pairs']==0
    assert summary['realized_trading_profit'] is None and summary['promotion_action']=='NONE'


def test_snapshot_size_and_cost_assumptions_are_bounded(monkeypatch):
    t=trial(monkeypatch)
    assert len(pilot.encoded(t).encode())<16384
    for patch in [{'fee_pct':'0'},{'slippage_pct':'NaN'},{'latency_pct':'0'}]:
        with pytest.raises(ValueError):pilot.checked_costs({**t['costs'],**patch})


def test_nonfinite_configured_cost_is_unknown_not_silently_replaced(monkeypatch):
    import paper_execution_reality
    monkeypatch.setattr(paper_execution_reality,'_market_defaults',lambda _: {'slippage_pct':float('nan'),'latency_pct':.001})
    with pytest.raises(ValueError):pilot.costs_at_entry()
