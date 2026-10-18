from contextlib import contextmanager
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import websocket

import kraken_reference_runtime as runtime
from kraken_reference_book import BookIntegrityError
from test_kraken_reference_book import NOW, ack, message


def socket_with(*messages):
    ws = Mock()
    ws.recv.side_effect = list(messages)
    return ws


def test_disconnect_resubscribe_requires_new_snapshot(monkeypatch):
    monkeypatch.setattr(runtime, "utc_now", lambda: NOW)
    first = socket_with(ack(), message(), "")
    second = socket_with(ack(), message(kind="update"))
    service = runtime.KrakenReferenceService(connect_factory=Mock(), socket_factory=Mock(side_effect=[first, second]))
    service.publish = Mock()
    with pytest.raises(BookIntegrityError, match="disconnected"): service._session(Mock())
    assert not service.state.books and not service.state.connected
    with pytest.raises(BookIntegrityError, match="snapshot_required"): service._session(Mock())
    assert json.loads(first.send.call_args_list[0].args[0])["params"] == {
        "channel": "book", "symbol": ["BTC/USD", "ETH/USD"], "depth": 10, "snapshot": True}
    assert first.close.called and second.close.called


@pytest.mark.parametrize("failure", ["checksum", "storage"])
def test_integrity_or_storage_failure_closes_socket(monkeypatch, failure):
    monkeypatch.setattr(runtime, "utc_now", lambda: NOW)
    ws = socket_with(ack(), message(checksum=0))
    service = runtime.KrakenReferenceService(connect_factory=Mock(), socket_factory=lambda *a,**k: ws)
    service.publish = Mock(side_effect=RuntimeError("storage unavailable") if failure == "storage" else None)
    with pytest.raises((BookIntegrityError, RuntimeError)): service._session(Mock())
    assert ws.close.called and not service.state.books


def test_liveness_timeout_without_messages(monkeypatch):
    monkeypatch.setattr(runtime, "utc_now", lambda: NOW)
    clock = iter([0, 16])
    monkeypatch.setattr(runtime.time, "monotonic", lambda: next(clock))
    ws = socket_with()
    ws.recv.side_effect = websocket.WebSocketTimeoutException()
    service = runtime.KrakenReferenceService(connect_factory=Mock(), socket_factory=lambda *a,**k: ws)
    service.publish = Mock()
    with pytest.raises(BookIntegrityError, match="liveness_timeout"): service._session(Mock())
    assert ws.close.called


def test_heartbeats_cannot_replace_snapshot(monkeypatch):
    monkeypatch.setattr(runtime, "utc_now", lambda: NOW)
    clock = iter([0, 16])
    monkeypatch.setattr(runtime.time, "monotonic", lambda: next(clock))
    ws = socket_with('{"channel":"heartbeat"}')
    service = runtime.KrakenReferenceService(connect_factory=Mock(), socket_factory=lambda *a,**k: ws)
    service.publish = Mock()
    with pytest.raises(BookIntegrityError, match="snapshot_timeout"): service._session(Mock())


def test_standby_worker_opens_no_websocket(monkeypatch):
    conn = Mock()
    @contextmanager
    def connect(): yield conn
    monkeypatch.setattr(runtime, "claim_owner", lambda c: False)
    factory = Mock()
    service = runtime.KrakenReferenceService(connect_factory=connect, socket_factory=factory)
    def wait(_): service.stop_event.set()
    monkeypatch.setattr(service.stop_event, "wait", wait)
    service._run()
    factory.assert_not_called()


def test_bounded_reconnect_loop_releases_owner_and_never_reuses_book(monkeypatch):
    conn = Mock()
    @contextmanager
    def connect(): yield conn
    monkeypatch.setattr(runtime, "claim_owner", lambda c: True)
    monkeypatch.setattr(runtime, "utc_now", lambda: NOW)
    service = runtime.KrakenReferenceService(connect_factory=connect)
    service.publish = Mock()
    calls = []
    def session(c):
        assert not service.state.books
        calls.append(c)
        if len(calls) == 3:
            service.stop_event.set()
        raise BookIntegrityError("subscription_error")
    service._session = session
    delays = []
    monkeypatch.setattr(service.stop_event, "wait", lambda seconds: delays.append(seconds))
    service._run()
    assert len(calls) == 3 and len(delays) == 2
    assert all(5 <= v <= 60 for v in delays)
    assert service.state.counts["reconnects"] == 2
    assert "pg_advisory_unlock" in conn.execute.call_args.args[0]


@pytest.mark.parametrize("env,value", [("EXECUTION_MODE", "live"), ("ENABLE_BROKER_SUBMISSION", "true"),
    ("LIVE_TRADING_ARMED", "true"), ("DATABASE_URL", ""), ("KRAKEN_REFERENCE_WS_ENABLED", "false")])
def test_install_safety_and_explicit_opt_in(monkeypatch, env, value):
    for key, val in {"EXECUTION_MODE": "paper", "ENABLE_BROKER_SUBMISSION": "false",
        "LIVE_TRADING_ARMED": "false", "DATABASE_URL": "test-placeholder", "KRAKEN_REFERENCE_WS_ENABLED": "true"}.items():
        monkeypatch.setenv(key, val)
    monkeypatch.setenv(env, value)
    factory = Mock(); monkeypatch.setattr(runtime, "KrakenReferenceService", factory)
    assert runtime.install_kraken_reference(SimpleNamespace()) is False
    factory.assert_not_called()


def test_observer_preserves_exact_primary_payload_and_none(monkeypatch):
    for key, val in {"EXECUTION_MODE": "paper", "ENABLE_BROKER_SUBMISSION": "false",
        "LIVE_TRADING_ARMED": "false", "DATABASE_URL": "test-placeholder", "KRAKEN_REFERENCE_WS_ENABLED": "true"}.items():
        monkeypatch.setenv(key, val)
    service = Mock(); monkeypatch.setattr(runtime, "KrakenReferenceService", lambda: service)
    payload = {"provider": "Robinhood Crypto", "verified": False, "bid": 102, "ask": 101}
    original = Mock(side_effect=[payload, None, payload])
    client = SimpleNamespace(best_bid_ask_quotes=original)
    execution = Mock(return_value=None)
    worker = SimpleNamespace(_execution_quote_payload_from_history=execution,
        _robinhood_current_marketdata_provider=SimpleNamespace(client=client))
    assert runtime.install_kraken_reference(worker)
    assert not runtime.install_kraken_reference(worker)
    assert client.best_bid_ask_quotes("BTC-USD") is payload
    assert client.best_bid_ask_quotes("BTC-USD") is None
    service.observe_response.side_effect = RuntimeError("diagnostic failure")
    assert client.best_bid_ask_quotes("BTC-USD") is payload
    assert worker._execution_quote_payload_from_history is execution
    execution.assert_not_called()
    assert payload == {"provider": "Robinhood Crypto", "verified": False, "bid": 102, "ask": 101}


def test_reader_never_opens_websocket_or_starts_ingestion(monkeypatch):
    import database
    @contextmanager
    def connect():
        raise RuntimeError("table unavailable")
        yield
    monkeypatch.setattr(database, "connect", connect)
    factory = Mock(); monkeypatch.setattr(runtime, "KrakenReferenceService", factory)
    assert runtime.read_kraken_reference_state()["status"] == "UNAVAILABLE_OR_DISABLED"
    factory.assert_not_called()
