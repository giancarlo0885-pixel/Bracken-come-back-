"""Synthetic timing regressions: analysis must not reuse an expired paper book."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from test_kraken_paper_pricing import install, NOW
from test_kraken_primary_research import history


def prepared(monkeypatch):
    p,q,worker,_,snapshot=install(monkeypatch)
    h=history();h.attrs.update(provider='Kraken Public OHLC',provider_route={
        'provider':'Kraken Public OHLC','interval':'5m','requested_symbol':'BTC-USD','provider_symbol':'BTC-USD'})
    p.data.history.return_value=h
    h=worker.get_history('BTC-USD','5d','5m')
    # A parallel scan/news/model pass consumes more than the receipt TTL.
    p.clock=lambda:NOW+timedelta(seconds=6)
    live=snapshot['quotes']['BTC-USD']
    live.update(quote_timestamp=(NOW+timedelta(seconds=6)).isoformat(),
                received_at=(NOW+timedelta(seconds=6)).isoformat())
    p.quote=Mock(wraps=p.quote)
    signal=SimpleNamespace(symbol='BTC-USD',price=99)
    return p,q,worker,snapshot,h,signal


def test_slow_analysis_captures_new_original_book_before_forecast_handoff(monkeypatch):
    p,old,worker,_,h,signal=prepared(monkeypatch)
    original=deepcopy(old);bars=h.copy();index=h.index.copy()
    route=worker._attach_execution_metadata(signal,h,'fast')
    packet=worker._execution_quote_payload_from_history('BTC-USD',h,signal.price,scan_type='fast')
    assert packet is not None
    assert packet['quote_id']!=old['quote_id'] and packet['quote_timestamp']==p.clock().isoformat()
    assert route['paper_quote']['quote_id']==packet['quote_id']
    assert signal.source_quote_timestamp==packet['quote_timestamp']
    assert signal.price==packet['price'] and signal.analysis_price==99
    assert p.authentic(packet,'BTC-USD') and not p.authentic(old,'BTC-USD')
    assert old==original and h.index.equals(index) and h.equals(bars)
    assert packet['provider_quote_verified'] is False
    p.quote.assert_called_once_with('BTC-USD')


def test_live_source_still_stale_cannot_be_rejuvenated(monkeypatch):
    p,old,worker,snapshot,h,signal=prepared(monkeypatch)
    snapshot['quotes']['BTC-USD']['quote_timestamp']=old['quote_timestamp']
    snapshot['quotes']['BTC-USD']['received_at']=old['received_at']
    worker._attach_execution_metadata(signal,h,'fast')
    assert worker._execution_quote_payload_from_history('BTC-USD',h,signal.price) is None
    assert h.attrs['provider_route']['paper_quote']['quote_timestamp']==old['quote_timestamp']


def test_already_created_forecast_cannot_be_rebound_to_later_quote(monkeypatch):
    p,old,worker,_,h,signal=prepared(monkeypatch)
    signal.forecast_id='original-forecast'
    worker._attach_execution_metadata(signal,h,'fast')
    assert worker._execution_quote_payload_from_history('BTC-USD',h,signal.price) is None
    assert h.attrs['provider_route']['paper_quote']['quote_id']==old['quote_id']
    p.quote.assert_not_called()


def test_yahoo_history_cannot_gain_kraken_pricing_handoff(monkeypatch):
    p,old,worker,_,h,signal=prepared(monkeypatch)
    h.attrs['provider']='Yahoo Finance';h.attrs['provider_route']['provider']='Yahoo Finance'
    h.attrs['provider_route'].pop('paper_quote')
    worker._attach_execution_metadata(signal,h,'fast')
    assert worker._execution_quote_payload_from_history('BTC-USD',h,signal.price) is None
    p.quote.assert_not_called()


def test_current_receipt_is_reused_and_raw_analysis_price_preserved(monkeypatch):
    p,_,worker,_,h,signal=prepared(monkeypatch)
    p.clock=lambda:NOW
    # Restore original cached reference times for this case.
    first=deepcopy(h.attrs['provider_route']['paper_quote'])
    p.reference=lambda:{'connected':True,'subscribed_symbols':['BTC-USD'],
        'quotes':{'BTC-USD':first['original_reference']}}
    worker._attach_execution_metadata(signal,h,'fast')
    worker._attach_execution_metadata(signal,h,'fast')
    assert signal.analysis_price==99
    p.quote.assert_not_called()
