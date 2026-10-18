from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
import json
import pytest

import kraken_reference_runtime as runtime
from kraken_venue_comparison import canonical_pair, robinhood_observations
from kraken_reference_book import compare_reference
from test_kraken_reference_book import NOW, ready, message


def test_raw_broker_observation_does_not_require_execution_handoff(monkeypatch):
    for key,value in {'KRAKEN_REFERENCE_WS_ENABLED':'true','EXECUTION_MODE':'paper',
        'ENABLE_BROKER_SUBMISSION':'false','LIVE_TRADING_ARMED':'false','DATABASE_URL':'local-test'}.items():
        monkeypatch.setenv(key,value)
    service = runtime.KrakenReferenceService(connect_factory=Mock())
    monkeypatch.setattr(service,'start',lambda: True)
    monkeypatch.setattr(runtime,'KrakenReferenceService',lambda: service)
    monkeypatch.setattr(runtime,'utc_now',lambda: NOW)
    records=[{'symbol':'BTC-USD','bid':'100','ask':'101','timestamp':NOW.isoformat()}]
    client=SimpleNamespace(best_bid_ask_quotes=Mock(return_value=records))
    execution=Mock(return_value=None)
    worker=SimpleNamespace(_robinhood_current_marketdata_provider=SimpleNamespace(client=client),
        _execution_quote_payload_from_history=execution)
    assert runtime.install_kraken_reference(worker)
    assert client.best_bid_ask_quotes('BTC-USD') is records
    execution.assert_not_called()
    assert service.primary['BTC-USD']['quote_timestamp']==NOW.isoformat()


@pytest.mark.parametrize('symbol,expected', [('BTC/USD','BTC-USD'),('eth-usd','ETH-USD'),
    ('ETH/USD','ETH-USD'),(' BTC-USD ','BTC-USD'),('XBT/USD',None),('BTC/EUR',None),('BTCUSD',None)])
def test_only_explicit_usd_pair_aliases(symbol,expected):
    assert canonical_pair(symbol)==expected


def quote(**changes):
    return {'symbol':'BTC-USD','bid':'100','ask':'101','timestamp':NOW.isoformat(),**changes}


@pytest.mark.parametrize('changes,reason', [
    ({'bid':'102'},'PRIMARY_CROSSED_BOOK'),({'bid':'NaN'},'PRIMARY_INVALID_BOOK'),
    ({'ask':'Infinity'},'PRIMARY_INVALID_BOOK'),({'bid':0},'PRIMARY_INVALID_BOOK'),
    ({'ask':None},'PRIMARY_INVALID_BOOK'),({'bid':True},'PRIMARY_INVALID_BOOK'),
    ({'timestamp':None},'PRIMARY_TIMESTAMP_MISSING'),({'timestamp':'bad'},'PRIMARY_TIMESTAMP_INVALID'),
    ({'timestamp':'2026-10-16T20:30:00'},'PRIMARY_TIMESTAMP_INVALID'),
])
def test_raw_observations_reject_bad_books_and_timestamps_without_rewriting(changes,reason):
    raw=quote(**changes); before=dict(raw)
    observed=robinhood_observations(['BTC-USD'],[raw],NOW)['BTC-USD']
    assert observed['comparison_rejection']==reason
    result=compare_reference(ready().quote('BTC/USD',NOW),observed,NOW)
    assert result['reason']==reason and 'mid_difference_bps' not in result
    assert raw==before


def test_raw_precedence_uses_same_parser_as_execution_and_preserves_precision():
    observed=robinhood_observations(['BTC-USD'],[quote(bid_price='102',ask_price='101')],NOW)
    assert observed['BTC-USD']['comparison_rejection']=='PRIMARY_CROSSED_BOOK'
    observed=robinhood_observations(['BTC-USD'],[quote(bid_price='100.00000001',ask_price='100.00000002')],NOW)
    assert observed['BTC-USD']['bid']=='100.00000001'
    assert observed['BTC-USD']['received_at']==NOW.isoformat()


@pytest.mark.parametrize('records,reason', [([], 'PRIMARY_SYMBOL_NOT_RETURNED'),
    ([quote(),quote()], 'PRIMARY_AMBIGUOUS_SYMBOL'),
    ([quote(symbol='ETH-USD')], 'PRIMARY_SYMBOL_NOT_RETURNED'),(None,'PRIMARY_REQUEST_FAILED')])
def test_missing_or_ambiguous_response_never_reuses_another_symbol(records,reason):
    assert robinhood_observations(['BTC-USD'],records,NOW)['BTC-USD']['comparison_rejection']==reason


def test_old_provider_time_cannot_be_refreshed_by_new_receipt():
    observed=robinhood_observations(['BTC-USD'],[quote(timestamp=(NOW-timedelta(seconds=11)).isoformat())],NOW)['BTC-USD']
    assert compare_reference(ready().quote('BTC/USD',NOW),observed,NOW)['reason']=='PRIMARY_STALE_OR_FUTURE'


def test_response_observation_preserves_newer_receipts_and_rejects_event_regression():
    service=runtime.KrakenReferenceService(connect_factory=Mock())
    service.observe_response(['BTC-USD'],[quote()],NOW)
    service.observe_response(['BTC-USD'],[quote(timestamp=(NOW-timedelta(seconds=1)).isoformat())],NOW+timedelta(seconds=1))
    assert service.primary['BTC-USD']['comparison_rejection']=='PRIMARY_TIMESTAMP_REGRESSION'
    service.observe_response(['BTC-USD'],[quote()],NOW)
    assert service.primary['BTC-USD']['comparison_rejection']=='PRIMARY_TIMESTAMP_REGRESSION'


def test_periodic_publication_pairs_retained_book_instead_of_five_second_newer_latest(monkeypatch):
    service=runtime.KrakenReferenceService(connect_factory=Mock());service.state=ready()
    service.remember_references(NOW)
    service.observe_response(['BTC-USD'],[quote()],NOW)
    later=NOW+timedelta(seconds=5)
    service.state.ingest(message(when=later),later)
    service.remember_references(later)
    monkeypatch.setattr(runtime,'utc_now',lambda: later)
    written=[];monkeypatch.setattr(runtime,'write_state',lambda c,p,n:written.append(p))
    service.publish(Mock()); payload=written[-1]
    comparison=payload['comparisons']['BTC-USD']
    assert comparison['status']=='ALIGNED_VENUE_COMPARISON'
    assert comparison['reference_timestamp']==NOW.isoformat()
    assert payload['quotes']['BTC-USD']['quote_timestamp']==later.isoformat()
    assert comparison['timestamp_delta_seconds']==0
    shown=runtime.display_state(payload,later,later)
    assert shown['comparisons']['BTC-USD']['reference_quote']['age_seconds']==5
    assert comparison['reference_quote']['execution_quote_eligible'] is False
    # Later publication cannot keep expired original comparison inputs alive.
    newer=NOW+timedelta(seconds=11)
    assert not runtime.display_state(payload,newer,newer)['comparisons']


def test_reference_history_is_bounded_and_cleared_on_integrity_failure():
    service=runtime.KrakenReferenceService(connect_factory=Mock());service.state=ready()
    for i in range(1500):
        now=NOW+timedelta(milliseconds=10*i)
        service.state.ingest(message(when=now),now)
        service.remember_references(now)
    history=service.reference_history['BTC-USD']
    assert 1 < len(history) <= 128
    assert (now-runtime.timestamp(history[0]['received_at'])).total_seconds()<=10
    service.state.invalidate();service.clear_references()
    assert not any(service.reference_history.values())


def test_observer_is_failure_isolated_and_adds_no_requests(monkeypatch):
    for key,value in {'KRAKEN_REFERENCE_WS_ENABLED':'true','EXECUTION_MODE':'paper',
        'ENABLE_BROKER_SUBMISSION':'false','LIVE_TRADING_ARMED':'false','DATABASE_URL':'local-test'}.items():
        monkeypatch.setenv(key,value)
    service=runtime.KrakenReferenceService(connect_factory=Mock());monkeypatch.setattr(service,'start',lambda:True)
    monkeypatch.setattr(runtime,'KrakenReferenceService',lambda:service)
    records=[quote()]; error=RuntimeError('original failure')
    original=Mock(side_effect=[records,error,records])
    client=SimpleNamespace(best_bid_ask_quotes=original)
    worker=SimpleNamespace(_robinhood_current_marketdata_provider=SimpleNamespace(client=client))
    assert runtime.install_kraken_reference(worker)
    assert client.best_bid_ask_quotes('BTC-USD') is records
    with pytest.raises(RuntimeError) as caught:client.best_bid_ask_quotes('BTC-USD')
    assert caught.value is error
    assert service.primary['BTC-USD']['comparison_rejection']=='PRIMARY_REQUEST_FAILED'
    monkeypatch.setattr(service,'observe_response',Mock(side_effect=RuntimeError('observer failure')))
    assert client.best_bid_ask_quotes('BTC-USD') is records
    assert original.call_count==3


def test_valid_observation_does_not_store_untrusted_or_private_fields():
    value=robinhood_observations(['BTC-USD'],[quote(token='private',account='secret')],NOW)['BTC-USD']
    assert 'private' not in json.dumps(value) and 'secret' not in json.dumps(value)


def test_equal_exchange_timestamps_use_latest_received_book(monkeypatch):
    service=runtime.KrakenReferenceService(connect_factory=Mock());service.state=ready()
    service.remember_references(NOW);service.observe_response(['BTC-USD'],[quote()],NOW)
    later=NOW+timedelta(seconds=1)
    service.state.ingest(message({'100.5':'1'},{'101.5':'1'},when=NOW),later)
    monkeypatch.setattr(runtime,'utc_now',lambda:later)
    written=[];monkeypatch.setattr(runtime,'write_state',lambda c,p,n:written.append(p))
    service.publish(Mock())
    paired=written[0]['comparisons']['BTC-USD']['reference_quote']
    assert paired['received_at']==later.isoformat() and paired['bid']=='100.5'
