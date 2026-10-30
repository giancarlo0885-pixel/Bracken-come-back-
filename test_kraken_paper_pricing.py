"""Synthetic adapter tests. No production fills, credentials or profitability claims."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
import threading
import pytest

import coinbase_paper_pricing as shared
import kraken_paper_pricing as pricing
from kraken_reference_book import ReferenceBooks
from kraken_research_data import ResearchUnavailable
from test_kraken_reference_book import NOW, ack, message


def source(symbol='BTC-USD'):
    state = ReferenceBooks({symbol.replace('-', '/'): symbol})
    state.opened(NOW)
    state.ingest(ack(symbol.replace('-', '/')), NOW)
    state.ingest(message({'100.00':'1.00000000', '99.0':'5.00'},
                         {'100.10':'1.00000000', '101.0':'5.00'}, symbol=symbol.replace('-', '/'), when=NOW), NOW)
    result = state.health(NOW)
    result['subscribed_symbols'] = [symbol]
    return result


def rules(symbol='BTC-USD'):
    return {'base':symbol[:-4], 'quote':'USD', 'aclass_base':'currency','aclass_quote':'currency',
            'status':'online','lot_multiplier':1,'lot_decimals':8,
            'ordermin':'0.00001','costmin':'0.5','tick_size':'0.01'}


def provider(monkeypatch, symbol='BTC-USD'):
    for key, value in {'CRYPTO_PAPER_PRICING_PROVIDER':'kraken','EXECUTION_MODE':'paper',
        'ENABLE_BROKER_SUBMISSION':'false','LIVE_TRADING_ARMED':'false',
        'KRAKEN_REFERENCE_WS_ENABLED':'true'}.items():monkeypatch.setenv(key,value)
    snapshot = source(symbol)
    data = SimpleNamespace(lock=threading.RLock(), instruments=Mock(return_value={symbol.replace('-', '/'):rules(symbol)}),
                           history=Mock(), last_receipt=NOW)
    p = pricing.KrakenPaperPricing(data, lambda:deepcopy(snapshot), lambda:NOW, publish=False)
    return p, snapshot


def checked(snapshot=None, symbol='BTC-USD'):
    snapshot = snapshot or source(symbol)
    return pricing.validate_kraken_book(symbol, snapshot['quotes'][symbol], rules(symbol), NOW.isoformat(), NOW)


@pytest.mark.parametrize('symbol',['BTC-USD','ETH-USD','AAVE-USD','ADA-USD'])
def test_exact_symbol_depth_crc_and_separate_reference_authority(monkeypatch,symbol):
    p,snapshot = provider(monkeypatch,symbol)
    before = deepcopy(snapshot)
    q = p.quote(symbol)
    assert q and p.authentic(q,symbol)
    assert q['provider']==pricing.SOURCE and q['paper_price_model']==pricing.MODEL
    assert q['original_reference']==snapshot['quotes'][symbol] and snapshot==before
    assert q['book']['bids'][0]==['100.00','1.00000000']
    assert q['independent_validation']=='NOT_REQUIRED_SINGLE_VENUE_PAPER_SIMULATION'
    assert q['paper_fee_pct']==.006 and q['fee_account_verified'] is False
    assert q['verified'] is False and q['provider_quote_verified'] is False
    assert 'sequence' not in q  # Kraken book v2 has no sequence number.
    assert q['quote_timestamp']==NOW.isoformat()==q['received_at']
    from quote_provenance import provider_verified,reference_allowed_in_mode
    assert not provider_verified(q) and reference_allowed_in_mode(q,'paper',True)
    assert not reference_allowed_in_mode(q,'live',True)
    assert not reference_allowed_in_mode(snapshot['quotes'][symbol],'paper',True)
    assert not p.authentic(snapshot['quotes'][symbol],symbol)


@pytest.mark.parametrize('field,value', [
    ('checksum',0), ('checksum_status','FAILED'), ('provider','Coinbase Exchange public reference'),
    ('reference_only',False), ('execution_quote_eligible',True), ('execution_authority','PAPER_SIMULATION_ONLY'),
    ('session_id',None), ('symbol','ETH-USD'), ('exchange_symbol','XBT/USD'), ('bid','99'),
    ('quote_timestamp',(NOW-timedelta(seconds=6)).isoformat()),
    ('quote_timestamp',(NOW+timedelta(milliseconds=1)).isoformat()),
    ('received_at',(NOW+timedelta(milliseconds=1)).isoformat()),
    ('received_at',(NOW-timedelta(seconds=6)).isoformat()), ('book',None),
])
def test_invalid_source_packet_never_registers_receipt(monkeypatch,field,value):
    p,snapshot=provider(monkeypatch)
    snapshot['quotes']['BTC-USD'][field]=value
    assert p.quote('BTC-USD') is None and not p.receipts


def test_precision_change_and_incomplete_depth_fail_crc(monkeypatch):
    p,snapshot=provider(monkeypatch)
    snapshot['quotes']['BTC-USD']['book']['bids'][0][1]='1'
    assert p.quote('BTC-USD') is None
    assert p.last_health['BTC-USD']['reason']=='KRAKEN_CHECKSUM_FAILURE'
    p,snapshot=provider(monkeypatch)
    snapshot['quotes']['BTC-USD']['book']['bids'].pop()
    assert p.quote('BTC-USD') is None


@pytest.mark.parametrize('field,value',[('status','cancel_only'),('base','ETH'),('ordermin','0'),
    ('costmin',None),('lot_decimals',True),('lot_multiplier',2),('tick_size','NaN')])
def test_unknown_or_ineligible_product_rules_fail_closed(monkeypatch,field,value):
    p,_=provider(monkeypatch)
    p.data.instruments.return_value['BTC/USD'][field]=value
    assert p.quote('BTC-USD') is None


def test_missing_disconnected_or_old_writer_does_not_gain_authority(monkeypatch):
    for change in ({'connected':False},{'quotes':{}},{'subscribed_symbols':[]}):
        p,snapshot=provider(monkeypatch);snapshot.update(change)
        assert p.quote('BTC-USD') is None
        p.data.instruments.assert_not_called()
    p,snapshot=provider(monkeypatch)
    del snapshot['quotes']['BTC-USD']['book']
    assert p.quote('BTC-USD') is None


@pytest.mark.parametrize('change',[{'EXECUTION_MODE':'live'},{'ENABLE_BROKER_SUBMISSION':'true'},
    {'LIVE_TRADING_ARMED':'true'},{'KRAKEN_REFERENCE_WS_ENABLED':'false'},
    {'CRYPTO_PAPER_PRICING_PROVIDER':'coinbase'}])
def test_no_adapter_permission_or_requests_outside_opted_in_paper(monkeypatch,change):
    p,_=provider(monkeypatch)
    for key,value in change.items():monkeypatch.setenv(key,value)
    assert p.quote('BTC-USD') is None and not p.authentic(checked(),'BTC-USD')
    p.data.instruments.assert_not_called()


def test_receipt_tampering_expiry_disconnect_and_new_session_fail_closed(monkeypatch):
    p,snapshot=provider(monkeypatch);q=p.quote('BTC-USD')
    for key,value in [('provider',shared.SOURCE),('price',99),('paper_fee_pct',0),
        ('checksum',1),('provider_quote_verified',True),('session_id','new'),
        ('quote_timestamp',(NOW+timedelta(seconds=1)).isoformat())]:
        assert not p.authentic({**q,key:value},'BTC-USD')
    snapshot['connected']=False
    assert not p.authentic(q,'BTC-USD')
    snapshot['connected']=True;snapshot['quotes']['BTC-USD']['session_id']='reconnected'
    assert not p.authentic(q,'BTC-USD')
    snapshot['quotes']['BTC-USD']['session_id']=q['session_id']
    p.clock=lambda:NOW+timedelta(seconds=6)
    assert not p.authentic(q,'BTC-USD')


def install(monkeypatch):
    import test_coinbase_paper_pricing as existing
    p,snapshot=provider(monkeypatch)
    monkeypatch.setattr(existing,'provider',lambda m:p)
    p,q,worker,original=existing.install(monkeypatch)
    return p,q,worker,original,snapshot


def test_depth_minimums_unknown_notional_and_separate_fee_once(monkeypatch):
    import paper_execution_reality as reality
    p,q,_,_,_=install(monkeypatch)
    vwap=float(shared.depth_price(q,'BUY',150))
    assert vwap>q['ask']
    fill=reality.simulate_fill(side='BUY',market='crypto',reference_price=q['price'],quote=q,
        order_value=150,slippage_pct=0,latency_pct=0,fee_pct=.006)
    assert fill.fill_price==pytest.approx(vwap)  # Fee is a separate cash charge.
    assert fill.fee_pct==.006 and fill.market_impact_pct==0
    for value in (None,0,-1,'NaN',.1,100000):
        with pytest.raises(ValueError):shared.depth_price(q,'BUY',value)
    assert float(shared.depth_price(q,'SELL',250))<q['bid']


def test_forecast_and_model_vetoes_survive_and_raw_reference_stays_blocked(monkeypatch):
    import oracle_bot
    p,q,_,original,snapshot=install(monkeypatch)
    signal={'symbol':'BTC-USD','signal_id':1,'forecast_id':'original'}
    guard=lambda packet:oracle_bot._execution_quote_guard('crypto','BTC-USD',q['price'],signal,quote_metadata=packet)
    monkeypatch.setattr(shared,'model_approved',lambda s:(False,'MODEL_GOVERNANCE_VETO'))
    assert guard(q)==(False,'MODEL_GOVERNANCE_VETO')
    monkeypatch.setattr(shared,'model_approved',lambda s:(True,''))
    monkeypatch.setattr(shared,'exact_forecast_link',lambda *a:False)
    assert guard(q)[1]=='ENTRY_FORECAST_LINK_INVALID'
    assert not original.called
    monkeypatch.setattr(shared,'exact_forecast_link',lambda *a:True)
    assert guard(q)[0] and not guard(snapshot['quotes']['BTC-USD'])[0]
    assert not oracle_bot._execution_quote_guard('crypto','BTC-USD',q['price'],{},quote_metadata=q)[0]
    assert oracle_bot._execution_quote_guard('cash','MSFT',100,{},quote_metadata={'provider':'Alpaca IEX'})[0]


def test_kraken_history_and_fresh_book_handoff_do_not_call_coinbase(monkeypatch):
    import market_worker
    from test_kraken_primary_research import history
    p,q,worker,_,_=install(monkeypatch)
    h=history();h.attrs.update(provider='Kraken Public OHLC',provider_route={'provider':'Kraken Public OHLC'})
    p.data.history.return_value=h
    result=worker.get_history('BTC-USD','5d','5m')
    p.data.history.assert_called_once_with('BTC-USD',NOW)
    assert result.attrs['provider_route']['quote_provider']==pricing.SOURCE
    assert result.attrs['provider_route']['analysis_bar_timestamp']!=q['quote_timestamp']
    worker._quote_payload_from_history=market_worker._quote_payload_from_history
    packet=worker._execution_quote_payload_from_history('BTC-USD',result,q['price'])
    assert packet['provider']==pricing.SOURCE and packet['provider_quote_verified'] is False


def graph():
    from test_paper_pricing_receipt_audit import receipt_graph
    fill,receipt,event,entry=receipt_graph()
    q=checked()
    receipt.update(quote_id=q['quote_id'],model_version=pricing.MODEL)
    receipt['evidence']['source_quote']=q
    execution=receipt['evidence']['execution']
    execution.update(reference_price=q['price'],depth_vwap=float(shared.depth_price(q,'BUY',100)))
    fill.update(quote_provider=pricing.SOURCE,reference_price=q['price'])
    entry['entry_quote_id']=q['quote_id']
    return fill,receipt,event,entry


def test_stored_original_kraken_receipt_audit_replays_crc_and_costs_without_network():
    from paper_pricing_receipt_audit import pricing_receipt_evidence
    fill,receipt,event,entry=graph()
    result=pricing_receipt_evidence(fill,[receipt],execution_time=event,entry=entry)
    assert result['status']=='CONSISTENT_ORIGINAL_PAPER_RECEIPT',result
    assert result['decision_time_commit_proof']=='NOT_ESTABLISHED'
    for field,value in [('book',{}),('checksum',0),('provider_quote_verified',True),('paper_fee_pct',0),
                        ('fee_account_verified',True),('quote_id','fake'),('original_reference',{})]:
        altered=deepcopy(receipt);altered['evidence']['source_quote'][field]=value
        assert pricing_receipt_evidence(fill,[altered],execution_time=event,entry=entry)['status']=='UNAVAILABLE'


def test_metadata_age_and_metadata_budget_failure_do_not_reset_or_relax_quota(monkeypatch):
    p,_=provider(monkeypatch);p.data.last_receipt=NOW-timedelta(seconds=301)
    assert p.quote('BTC-USD') is None
    p.data.instruments.side_effect=ResearchUnavailable('PROVIDER_BUDGET_EXHAUSTED')
    assert p.quote('BTC-USD') is None
    assert p.last_health['BTC-USD']['reason']=='PROVIDER_BUDGET_EXHAUSTED'


def test_quote_measurement_uses_checksum_session_not_invented_sequence(monkeypatch):
    from datetime import datetime
    import paper_regime_economics_shadow as regime
    monkeypatch.setattr(regime,'active',lambda:True)
    monkeypatch.setattr(regime,'_QUOTE_OBSERVATIONS',{})
    class Clock(datetime):
        @classmethod
        def now(cls,tz):return NOW
    monkeypatch.setattr(regime,'datetime',Clock)
    p,_=provider(monkeypatch);q=p.quote('BTC-USD')
    observed=regime._cached_observation('crypto','BTC-USD')
    assert observed and observed['observed_at']==NOW
    assert '"checksum"' in observed['source'] and '"sequence"' not in observed['source']
    assert not regime.observe_paper_quote(p,q)


@pytest.mark.parametrize('bid,ask',[('100.00','100.00'),('101.00','100.00')])
def test_checksum_valid_locked_or_crossed_books_are_not_simulatable(bid,ask):
    from decimal import Decimal
    from kraken_reference_book import book_checksum
    snapshot=source();q=snapshot['quotes']['BTC-USD']
    q['book']={'bids':[[bid,'1.00000000']],'asks':[[ask,'1.00000000']]}
    q['checksum']=book_checksum({Decimal(bid):Decimal('1.00000000')},{Decimal(ask):Decimal('1.00000000')})
    with pytest.raises(ResearchUnavailable,match='CROSSED_OR_LOCKED'):checked(snapshot)


def test_real_quote_contract_accepts_only_registered_paper_receipt(monkeypatch):
    import test_coinbase_paper_pricing as existing
    import oracle_bot,paper_execution_reality
    p,snapshot=provider(monkeypatch)
    monkeypatch.setattr(existing,'provider',lambda m:p)
    monkeypatch.setattr(oracle_bot,'quote_is_fresh',lambda *a,**k:True)
    monkeypatch.setattr(oracle_bot,'EXECUTION_MODE','paper')
    monkeypatch.setattr(oracle_bot,'PAPER_BROKER_MODE',True)
    p,q,worker,_=existing.install(monkeypatch,real_contract=True)
    monkeypatch.setattr(shared,'model_approved',lambda s:(True,''))
    monkeypatch.setattr(shared,'exact_forecast_link',lambda *a:True)
    signal={'symbol':'BTC-USD','signal_id':1,'forecast_id':'original'}
    assert oracle_bot._verified_quote_for('BTC-USD',{'BTC-USD':q},'crypto')['provider']==pricing.SOURCE
    adjusted,fill=paper_execution_reality._execution_quote(market='crypto',side='BUY',reference_price=q['price'],quote=q,order_value=150)
    assert oracle_bot._execution_quote_guard('crypto','BTC-USD',fill.fill_price,signal,quote_metadata=adjusted)[0]
    assert not oracle_bot._execution_quote_guard('crypto','BTC-USD',q['price'],signal,quote_metadata=snapshot['quotes']['BTC-USD'])[0]
    monkeypatch.setattr(oracle_bot,'EXECUTION_MODE','live')
    assert not oracle_bot._execution_quote_guard('crypto','BTC-USD',fill.fill_price,signal,quote_metadata=adjusted)[0]


def test_disconnected_reference_does_not_publish_successful_price(monkeypatch):
    p,snapshot=provider(monkeypatch)
    assert p.quote('BTC-USD')
    snapshot['connected']=False
    assert p.quote('BTC-USD') is None
    assert p.last_health['BTC-USD']['status']=='UNAVAILABLE'


def test_worker_selection_chooses_kraken_and_unknown_selection_fails_closed(monkeypatch):
    import oracle_bot,market_data,paper_execution_reality,market_worker
    p,_=provider(monkeypatch)
    monkeypatch.setattr(pricing,'KrakenPaperPricing',lambda:p)
    # Save wrapped globals so this installation cannot leak to other tests.
    for module,name in ((oracle_bot,'_execution_quote_guard'),(oracle_bot,'_verified_quote_for'),
            (oracle_bot,'simulate_fill'),(market_data,'get_live_snapshot'),(paper_execution_reality,'simulate_fill')):
        monkeypatch.setattr(module,name,getattr(module,name))
    worker=SimpleNamespace(get_history=Mock(),_execution_quote_payload_from_history=Mock(),
        _attach_execution_metadata=market_worker._attach_execution_metadata,get_many_snapshots=Mock(),
        _quote_payload_from_history=Mock(return_value={}),_v39_signal_opportunity=Mock())
    assert shared.install_paper_pricing(worker)
    assert worker._coinbase_paper_pricing is p
    monkeypatch.setenv('CRYPTO_PAPER_PRICING_PROVIDER','unknown-provider')
    assert not shared.install_paper_pricing(SimpleNamespace())


def test_selected_single_venue_paper_model_does_not_claim_missing_execution_provider(monkeypatch):
    from test_kraken_publication_timing import setup_publication
    import json
    service,conn,_,_,_,stored,_=setup_publication(monkeypatch)
    provider(monkeypatch)
    service.primary_observer_available=False
    service.publish(conn)
    payload=json.loads(stored[0][1][1])
    assert all(c['status']=='SINGLE_VENUE_PAPER_MODEL_SELECTED' for c in payload['comparisons'].values())
    assert payload['execution_authority']=='NONE'
