from copy import deepcopy
from datetime import datetime,timedelta,timezone
from unittest.mock import Mock
import pytest
from paper_trade_evidence_audit import entry_reference_reasons, emit_trade_evidence_audit, summarize_trade_evidence

NOW=datetime(2026,10,18,tzinfo=timezone.utc)


def entry():
    e={'market':'crypto','symbol':'TEST-USD','entry_time':NOW.isoformat(),
       'decision_timestamp':(NOW-timedelta(seconds=1)).isoformat(),'entry_signal_id':'1',
       'entry_decision_id':'decision','entry_forecast_id':'forecast','entry_quote_id':'quote',
       'feature_snapshot':{'momentum':.1}}
    return e,deepcopy(e),[{'id':1,'market':'crypto','symbol':'TEST-USD','created_at':e['decision_timestamp']}],[
        {'forecast_id':'forecast','signal_id':1,'market':'crypto','symbol':'TEST-USD',
         'created_at':e['decision_timestamp'],'source_quote_timestamp':(NOW-timedelta(seconds=2)).isoformat()}]


def test_exact_entry_reference_resolves_without_latest_lookup():
    assert entry_reference_reasons(*entry())==[]


@pytest.mark.parametrize('field',['entry_signal_id','entry_decision_id','entry_forecast_id','entry_quote_id'])
def test_lot_conflicts_are_not_repaired(field):
    e,lot,s,f=entry();lot[field]='different'
    assert 'lot_entry_reference_mismatch' in entry_reference_reasons(e,lot,s,f)
    assert lot[field]=='different'


@pytest.mark.parametrize('target,field,value,reason',[
    ('lot','feature_snapshot',{'momentum':True},'lot_entry_snapshot_mismatch'),
    ('lot','decision_timestamp',NOW.isoformat(),'lot_entry_decision_time_mismatch'),
    ('signal','symbol','OTHER-USD','unresolved_or_ambiguous_entry_signal'),
    ('signal','market','cash','unresolved_or_ambiguous_entry_signal'),
    ('signal','created_at',(NOW+timedelta(seconds=1)).isoformat(),'late_or_invalid_entry_signal'),
    ('forecast','signal_id',2,'unresolved_or_ambiguous_entry_forecast'),
    ('forecast','symbol','OTHER-USD','unresolved_or_ambiguous_entry_forecast'),
    ('forecast','created_at',(NOW+timedelta(seconds=1)).isoformat(),'late_or_invalid_entry_forecast'),
    ('forecast','source_quote_timestamp',NOW.isoformat(),'late_or_invalid_entry_forecast')])
def test_reference_dimensions_and_chronology(target,field,value,reason):
    e,lot,s,f=entry();{'lot':lot,'signal':s[0],'forecast':f[0]}[target][field]=value
    assert reason in entry_reference_reasons(e,lot,s,f)


def test_missing_and_duplicate_forecasts_are_not_qualified():
    e,lot,s,f=entry()
    assert 'unresolved_or_ambiguous_entry_forecast' in entry_reference_reasons(e,lot,s,f*2)
    e['entry_forecast_id']=lot['entry_forecast_id']=None
    assert 'missing_entry_forecast_id' in entry_reference_reasons(e,lot,s,[])


@pytest.mark.parametrize('key,value',[('EXECUTION_MODE','live'),('ENABLE_BROKER_SUBMISSION','true'),('LIVE_TRADING_ARMED','true')])
def test_audit_disarmed_boundary(monkeypatch,key,value):
    for k,v in {'EXECUTION_MODE':'paper','ENABLE_BROKER_SUBMISSION':'false','LIVE_TRADING_ARMED':'false'}.items():monkeypatch.setenv(k,v)
    monkeypatch.setenv(key,value);connect=Mock()
    assert emit_trade_evidence_audit('crypto',connect_factory=connect)['status']=='DISABLED'
    connect.assert_not_called()


def test_invalid_market_and_limit_fail_before_queries():
    conn=Mock()
    with pytest.raises(ValueError):summarize_trade_evidence(conn,market='live')
    with pytest.raises(ValueError):summarize_trade_evidence(conn,market='crypto',limit=51)
    conn.execute.assert_not_called()


@pytest.mark.parametrize('field,reason',[('entry_decision_id','missing_entry_decision_id'),
    ('entry_quote_id','missing_entry_quote_id'),('feature_snapshot','missing_entry_features')])
def test_missing_entry_evidence_is_not_made_complete_by_matching_nulls(field,reason):
    e,lot,s,f=entry();e[field]=lot[field]=None
    assert reason in entry_reference_reasons(e,lot,s,f)
