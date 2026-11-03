"""Synthetic OHLC stream tests; no private credentials, orders or production SQL."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
import json
import pytest

from kraken_streamed_candles import CandleStream, enabled, MAX_ROWS
from kraken_research_data import PublicResearchData
from test_kraken_reference_book import NOW,ack as book_ack,message as book_message
from test_kraken_primary_research import history, rows

SYMBOLS={'BTC/USD':'BTC-USD'}


def flags(monkeypatch):
    for k,v in {'KRAKEN_OHLC_WS_ENABLED':'true','KRAKEN_REFERENCE_WS_ENABLED':'true',
        'EXECUTION_MODE':'paper','ENABLE_BROKER_SUBMISSION':'false','LIVE_TRADING_ARMED':'false'}.items():monkeypatch.setenv(k,v)


def ack():
    return {'method':'subscribe','success':True,'result':{'channel':'ohlc','symbol':'BTC/USD','interval':5,'snapshot':True}}


def bar(at=NOW,**kw):
    return {'symbol':'BTC/USD','interval':5,'interval_begin':at.isoformat(),
        'open':'100','high':'101','low':'99','close':'100','volume':'2','vwap':'100','trades':2,**kw}


def packet(at=NOW,kind='update',**kw):
    return {'channel':'ohlc','type':kind,'data':[bar(at,**kw)]}


def ready():
    s=CandleStream();s.opened(SYMBOLS,NOW);s.consume(ack(),NOW);s.consume(packet(kind='snapshot'),NOW)
    assert s.seed('BTC-USD',history(),NOW,NOW,expected_session=s.generation())
    return s


def test_real_bootstrap_is_preserved_and_forming_candle_never_enters_forecast():
    s=ready();h=s.history('BTC-USD',NOW)
    assert len(h)==60 and h.index[-1].to_pydatetime()==NOW-timedelta(minutes=5)
    before=h.copy(deep=True);attributes=deepcopy(h.attrs)
    s.consume(packet(close='101',volume='3'),NOW+timedelta(seconds=1))
    assert s.history('BTC-USD',NOW+timedelta(seconds=1)).equals(before)
    at=NOW+timedelta(minutes=5);s.consume(packet(at,open='1000',high='1001',low='999',close='1000'),at)
    h2=s.history('BTC-USD',at)
    assert len(h2)==61 and h2.iloc[-1].Close==101 and h2.iloc[-1].Volume==3
    assert h2.index[-1].to_pydatetime()==NOW
    assert h.equals(before) and h.attrs==attributes
    assert h2.attrs['quote_verified'] is False and h2.attrs['execution_quote_eligible'] is False
    assert h2.attrs['provider_route']['transport']=='REST_BOOTSTRAP_AND_WEBSOCKET_V2_OHLC'
    assert h2.attrs['provider_route']['quote_timestamp']==NOW.isoformat()
    assert h2.attrs['provider_route']['ohlc_crc_status']=='NOT_PROVIDED_NOT_ORDER_BOOK_VERIFICATION'


def test_heartbeat_does_not_close_candle_or_renew_old_history():
    s=ready();at=NOW+timedelta(minutes=11)
    s.wire(at)
    assert s.history('BTC-USD',at) is None
    assert s.latest['BTC-USD']==NOW


@pytest.mark.parametrize('change',[{'close':'NaN'},{'close':True},{'high':'90'},
    {'volume':'-1'},{'interval':True},{'interval':1},{'symbol':'ETH/USD'},
    {'interval_begin':(NOW+timedelta(minutes=5)).isoformat()}])
def test_invalid_updates_invalidate_stream_history_without_invented_data(change):
    s=ready();s.consume(packet(**change),NOW)
    assert s.history('BTC-USD',NOW) is None
    assert not s.connected and not s.seeded


def test_same_interval_trade_count_regression_invalidates_optional_history():
    # Kraken's trades field is cumulative within an interval. An update with
    # higher volume but fewer trades cannot be a valid incremental candle.
    s = ready()
    s.consume(packet(volume='3', trades=3), NOW + timedelta(seconds=1))
    assert s.connected
    s.consume(packet(volume='4', trades=2), NOW + timedelta(seconds=2))
    assert not s.connected
    assert s.history('BTC-USD', NOW + timedelta(seconds=2)) is None


def test_cumulative_trade_count_resets_only_at_new_interval():
    s = ready()
    s.consume(packet(volume='3', trades=5), NOW + timedelta(seconds=1))
    s.consume(packet(NOW + timedelta(minutes=5), trades=1), NOW + timedelta(minutes=5))
    assert s.connected
    assert len(s.history('BTC-USD', NOW + timedelta(minutes=5))) == 61


def test_closed_candle_trade_count_revision_requires_resnapshot():
    s = ready()
    s.consume(packet(NOW + timedelta(minutes=5)), NOW + timedelta(minutes=5))
    assert s.connected
    # Identical OHLC/volume cannot conceal a changed cumulative trade count.
    s.consume(packet(NOW, trades=3), NOW + timedelta(minutes=5, seconds=1))
    assert not s.connected
    assert s.history('BTC-USD', NOW + timedelta(minutes=5, seconds=1)) is None


def test_gap_closed_revision_and_volume_regression_require_resnapshot():
    for kind in ('gap','revision','regression'):
        s=ready()
        if kind=='gap':s.consume(packet(NOW+timedelta(minutes=10)),NOW+timedelta(minutes=10))
        elif kind=='revision':s.consume(packet(NOW-timedelta(minutes=5),close='100.5'),NOW)
        else:s.consume(packet(volume='1'),NOW)
        assert s.history('BTC-USD',NOW) is None
        s.consume(packet(kind='snapshot'),NOW)  # A packet cannot resurrect a disconnected generation.
        assert s.history('BTC-USD',NOW) is None


def test_disconnect_requires_new_ack_snapshot_and_real_bootstrap():
    s=ready();old=s.generation();s.invalidate();s.opened(SYMBOLS,NOW)
    assert s.generation()!=old and s.history('BTC-USD',NOW) is None
    s.consume(ack(),NOW);s.consume(packet(kind='snapshot'),NOW)
    assert not s.seed('BTC-USD',history(),NOW,NOW,expected_session=old)
    assert s.history('BTC-USD',NOW) is None
    assert s.seed('BTC-USD',history(),NOW,NOW,expected_session=s.generation())


def test_unacknowledged_update_or_subscription_error_cannot_seed():
    for message in [packet(),{**ack(),'success':False}]:
        s=CandleStream();s.opened(SYMBOLS,NOW);s.consume(message,NOW)
        assert not s.seed('BTC-USD',history(),NOW,NOW,expected_session=s.generation())


def test_bounded_memory_and_five_minute_closed_history():
    s=ready()
    for i in range(1,MAX_ROWS+20):
        at=NOW+timedelta(minutes=5*i);s.consume(packet(at),at)
    h=s.history('BTC-USD',at)
    assert len(s.rows['BTC-USD'])==MAX_ROWS and len(h)==720
    assert len(s.trade_counts['BTC-USD'])==MAX_ROWS
    with pytest.raises(ValueError):s.opened({str(i):str(i) for i in range(5)},NOW)


@pytest.mark.parametrize('key,value',[('KRAKEN_OHLC_WS_ENABLED',None),('EXECUTION_MODE',None),
    ('ENABLE_BROKER_SUBMISSION',None),('LIVE_TRADING_ARMED',None),('EXECUTION_MODE','live'),
    ('ENABLE_BROKER_SUBMISSION','true'),('LIVE_TRADING_ARMED','true')])
def test_feature_requires_explicit_safe_opt_in(monkeypatch,key,value):
    flags(monkeypatch)
    if value is None:monkeypatch.delenv(key)
    else:monkeypatch.setenv(key,value)
    assert not enabled()


def test_client_bootstraps_once_then_next_interval_needs_no_rest(monkeypatch):
    flags(monkeypatch);s=ready()
    # A separate ready generation that still needs its original REST bootstrap.
    s.opened(SYMBOLS,NOW);s.consume(ack(),NOW);s.consume(packet(kind='snapshot'),NOW)
    client=PublicResearchData();client.ohlc_stream=s;client.last_receipt=NOW
    client.request=Mock(return_value={'BTC/USD':rows(),'last':1})
    import kraken_research_data as data
    monkeypatch.setattr(data,'datetime',SimpleNamespace(now=lambda *_:NOW))
    first=client.history('BTC-USD',NOW)
    at=NOW+timedelta(minutes=5);s.consume(packet(at),at)
    second=client.history('BTC-USD',at)
    assert client.request.call_count==1 and len(second)==len(first)+1
    assert client.last_receipt==NOW  # A stream hit never refreshes a REST/metadata receipt.
    s.invalidate();client.history('BTC-USD',NOW)
    assert client.request.call_count==2  # Existing bounded REST fallback remains.


def test_book_and_candle_channels_share_socket_but_not_authority(monkeypatch):
    flags(monkeypatch)
    from kraken_reference_runtime import KrakenReferenceService
    service=KrakenReferenceService(connect_factory=Mock(),socket_factory=Mock())
    service.symbols=SYMBOLS;service.state.symbols=SYMBOLS
    service.state.opened(NOW);service.candles.opened(SYMBOLS,NOW)
    service._ingest_wire(book_ack(),NOW);service._ingest_wire(book_message(),NOW)
    original=service.state.quote('BTC/USD',NOW)
    service._ingest_wire(json.dumps(ack()),NOW)
    service._ingest_wire(json.dumps(packet(kind='snapshot')),NOW)
    service._ingest_wire(json.dumps(packet(close='NaN')),NOW)
    assert service.state.quote('BTC/USD',NOW)==original
    assert service.candles.history('BTC-USD',NOW) is None
    assert original['execution_authority']=='NONE'


def test_installer_shares_existing_data_client_without_new_ingestion_owner(monkeypatch):
    flags(monkeypatch);monkeypatch.setenv('DATABASE_URL','postgresql://localhost/disposable_test')
    import kraken_reference_runtime as runtime
    monkeypatch.setattr(runtime.KrakenReferenceService,'start',Mock())
    client=PublicResearchData();worker=SimpleNamespace(WATCHLISTS={'crypto':{'BTC-USD':'Bitcoin'}},
        _coinbase_paper_pricing=SimpleNamespace(source='Kraken WebSocket v2 paper pricing',data=client))
    assert runtime.install_kraken_reference(worker)
    assert client.ohlc_stream is worker._kraken_reference_service.candles
    assert not runtime.install_kraken_reference(worker)
    runtime.KrakenReferenceService.start.assert_called_once()


def test_bootstrap_is_not_seeded_if_socket_generation_changes_during_request(monkeypatch):
    flags(monkeypatch);s=ready();s.opened(SYMBOLS,NOW);s.consume(ack(),NOW);s.consume(packet(kind='snapshot'),NOW)
    client=PublicResearchData();client.ohlc_stream=s;client.last_receipt=NOW
    def request(*_):
        s.invalidate();s.opened(SYMBOLS,NOW);s.consume(ack(),NOW);s.consume(packet(kind='snapshot'),NOW)
        return {'BTC/USD':rows(),'last':1}
    client.request=request
    import kraken_research_data as data
    monkeypatch.setattr(data,'datetime',SimpleNamespace(now=lambda *_:NOW))
    h=client.history('BTC-USD',NOW)
    assert len(h)==60 and not s.seeded
    assert 'transport' not in h.attrs['provider_route']  # Original REST history, not a continuous-stream claim.


def test_failed_candle_ack_without_result_does_not_invalidate_valid_book(monkeypatch):
    flags(monkeypatch)
    from kraken_reference_runtime import KrakenReferenceService
    s=KrakenReferenceService(connect_factory=Mock(),socket_factory=Mock())
    s.symbols=SYMBOLS;s.state.symbols=SYMBOLS;s.state.opened(NOW);s.candles.opened(SYMBOLS,NOW)
    s._ingest_wire(book_ack(),NOW);s._ingest_wire(book_message(),NOW)
    original=s.state.quote('BTC/USD',NOW)
    s._ingest_wire(json.dumps({'method':'subscribe','req_id':3,'success':False,'error':'unavailable'}),NOW)
    assert s.state.quote('BTC/USD',NOW)==original
    assert s.candles.history('BTC-USD',NOW) is None and not s.candles.connected


def test_bootstrap_receipt_is_not_replaced_by_concurrent_metadata_receipt(monkeypatch):
    flags(monkeypatch);s=CandleStream();s.opened(SYMBOLS,NOW);s.consume(ack(),NOW);s.consume(packet(kind='snapshot'),NOW)
    client=PublicResearchData();client.ohlc_stream=s;client.last_receipt=NOW
    client.request=Mock(return_value={'BTC/USD':rows(),'last':1})
    import kraken_research_data as data
    validate=data.validate_closed_history
    def later_other_endpoint_receipt(frame,now):
        validate(frame,now)
        client.last_receipt=NOW+timedelta(seconds=1)
    monkeypatch.setattr(data,'validate_closed_history',later_other_endpoint_receipt)
    monkeypatch.setattr(data,'datetime',SimpleNamespace(now=lambda *_:NOW))
    h=client.history('BTC-USD',NOW)
    assert h.attrs['provider_route']['bootstrap_received_at']==NOW.isoformat()
    assert client.last_receipt==NOW+timedelta(seconds=1)


@pytest.mark.parametrize('received',[None,NOW+timedelta(seconds=1),NOW-timedelta(seconds=1)])
def test_missing_future_or_preclose_bootstrap_receipt_cannot_seed(received):
    s=CandleStream();s.opened(SYMBOLS,NOW);s.consume(ack(),NOW);s.consume(packet(kind='snapshot'),NOW)
    assert not s.seed('BTC-USD',history(),NOW,received,expected_session=s.generation())
    assert s.history('BTC-USD',NOW) is None


def test_elected_session_uses_one_socket_and_clears_both_channels_on_disconnect(monkeypatch):
    flags(monkeypatch)
    import kraken_reference_runtime as runtime
    from kraken_reference_book import BookIntegrityError
    from test_kraken_reference_runtime import socket_with
    monkeypatch.setattr(runtime,'utc_now',lambda:NOW)
    ws=socket_with(book_ack(),book_message(),json.dumps(ack()),json.dumps(packet(kind='snapshot')),'')
    factory=Mock(return_value=ws)
    s=runtime.KrakenReferenceService(connect_factory=Mock(),socket_factory=factory)
    s.symbols=SYMBOLS;s.state.symbols=SYMBOLS;s.publish=Mock()
    with pytest.raises(BookIntegrityError,match='disconnected'):s._session(Mock())
    factory.assert_called_once()
    sent=[json.loads(call.args[0]) for call in ws.send.call_args_list]
    assert [m['params']['channel'] for m in sent if m['method']=='subscribe']==['book','ohlc']
    assert sent[1]['params']['interval']==5 and sent[1]['params']['snapshot'] is True
    assert ws.close.called and not s.state.books and not s.candles.connected
    assert s.candles.updates==1 and not s.candles.seeded
