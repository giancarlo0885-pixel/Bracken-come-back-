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


from paper_trade_evidence_audit import forecast_generation_evidence


def test_recorded_abstention_is_not_inferred_as_lost_forecast():
    e,lot,s,f=entry();e['entry_forecast_id']=None
    s[0]['details']={'forecast_unavailable_reason':'stale_live_quote_timestamp'}
    assert forecast_generation_evidence(e,s,[]) == {
        'status':'RECORDED_FORECAST_UNAVAILABLE','unavailable_reason':'stale_live_quote_timestamp'}
    assert forecast_generation_evidence(e,[],[]) == {'status':'UNKNOWN_SIGNAL_UNRESOLVED'}


def test_persisted_exact_signal_forecast_identifies_missing_link_without_repair():
    e,lot,s,f=entry();e['entry_forecast_id']=None
    assert forecast_generation_evidence(e,s,f)['status']=='PERSISTED_FORECAST_MISSING_ENTRY_LINK'
    assert e['entry_forecast_id'] is None
    assert forecast_generation_evidence(e,s,f*2)['status']=='AMBIGUOUS_PERSISTED_FORECASTS'


def test_missing_record_does_not_claim_insert_failure_or_retention_loss():
    e,lot,s,f=entry()
    assert forecast_generation_evidence(e,s,[])['status']=='REFERENCED_FORECAST_NOT_FOUND'
    e['entry_forecast_id']=None
    assert forecast_generation_evidence(e,s,[])['status']=='UNKNOWN_NO_GENERATION_RECEIPT'
    s[0]['details']='{"forecast_id":"original"}'
    assert forecast_generation_evidence(e,s,[])['status']=='REFERENCED_FORECAST_NOT_FOUND'


@pytest.mark.parametrize('field,value',[
    ('signal_id',99),('symbol','OTHER-USD'),('market','cash'),
    ('created_at',(NOW+timedelta(seconds=1)).isoformat())])
def test_other_or_later_forecasts_cannot_explain_original_entry(field,value):
    e,lot,s,f=entry();e['entry_forecast_id']=None;f[0][field]=value
    assert forecast_generation_evidence(e,s,f)['status']=='UNKNOWN_NO_GENERATION_RECEIPT'


def test_conflicts_invalid_quotes_and_late_signals_stay_unknown():
    e,lot,s,f=entry()
    assert forecast_generation_evidence(e,s,f)['status']=='PERSISTED_EXACT_ENTRY_LINK'
    s[0]['details']={'forecast_unavailable_reason':'causal_model_abstention'}
    assert forecast_generation_evidence(e,s,f)['status']=='CONFLICTING_GENERATION_EVIDENCE'
    s[0]['details']={};f[0]['source_quote_timestamp']=NOW.isoformat()
    assert forecast_generation_evidence(e,s,f)['status']=='UNKNOWN_INVALID_FORECAST_EVIDENCE'
    s[0]['created_at']=(NOW+timedelta(seconds=1)).isoformat()
    assert forecast_generation_evidence(e,s,[])['status']=='UNKNOWN_INVALID_SIGNAL_TIME'


@pytest.mark.parametrize('details',[None, 'not json', ['not a receipt'], {'forecast_unavailable_reason':False}])
def test_malformed_receipt_cannot_become_proof_of_abstention(details):
    e,lot,s,f=entry();e['entry_forecast_id']=None;s[0]['details']=details
    assert forecast_generation_evidence(e,s,[])['status']=='UNKNOWN_NO_GENERATION_RECEIPT'


def test_conflicting_original_forecast_id_is_not_diagnosed_as_a_recoverable_link():
    e,lot,s,f=entry();e['entry_forecast_id']=None
    s[0]['details']={'forecast_id':'different-original-forecast'}
    assert forecast_generation_evidence(e,s,f)['status']=='CONFLICTING_GENERATION_EVIDENCE'
