"""Read-only attribution of Kraken stale source clocks before fail-closed reconnect."""
from datetime import timedelta
import json
from unittest.mock import Mock

import pytest

import kraken_reference_runtime as runtime
from kraken_reference_book import BookIntegrityError, ReferenceBooks
from test_kraken_reference_book import NOW, ack, message


def ready_two():
    state = ReferenceBooks()
    state.opened(NOW)
    for symbol in ("BTC/USD", "ETH/USD"):
        state.ingest(ack(symbol), NOW)
        state.ingest(message(symbol=symbol), NOW)
    return state


def test_bounded_source_report_exposes_actual_stale_symbol_only():
    state = ready_two()
    late = NOW + timedelta(seconds=8)
    state.ingest(message(symbol="ETH/USD", kind="update", bids={}, asks={}, when=late), late)
    report = runtime.stale_book_source_diagnostic(state, NOW + timedelta(seconds=11))
    assert report == [{
        "symbol": "BTC-USD",
        "reason": "EXCHANGE_TIMESTAMP_EXPIRED",
        "exchange_age_seconds": 11.0, "receipt_age_seconds": 11.0,
    }]
    assert state.quote("ETH/USD", NOW + timedelta(seconds=11)) is not None
    assert state.quote("BTC/USD", NOW + timedelta(seconds=11)) is None
    assert state.counts["stale_rejections"] == 1


def test_expired_receipt_is_distinguished_from_exchange_time():
    state = ready_two()
    state.books["BTC/USD"]["received_time"] = NOW + timedelta(seconds=5)
    report = runtime.stale_book_source_diagnostic(state, NOW + timedelta(seconds=2))
    assert report == [{
        "symbol": "BTC-USD",
        "reason": "RECEIPT_TIMESTAMP_EXPIRED",
        "exchange_age_seconds": 2.0, "receipt_age_seconds": -3.0,
    }]
    # Diagnostic does not replace original receipt with an invented newer stamp.
    assert state.books["BTC/USD"]["received_time"] == NOW + timedelta(seconds=5)


def test_stale_after_heartbeat_logs_affected_market_then_clears_books(monkeypatch, caplog):
    moment = [NOW]
    monkeypatch.setattr(runtime, "utc_now", lambda: moment[0])
    events = iter([ack("BTC/USD"), ack("ETH/USD"),
                   message(symbol="BTC/USD"), message(symbol="ETH/USD"),
                   '{"channel":"heartbeat"}'])
    def recv():
        raw = next(events)
        if raw == '{"channel":"heartbeat"}':
            moment[0] = NOW + timedelta(seconds=11)
        return raw
    ws = Mock()
    ws.recv.side_effect = recv
    service = runtime.KrakenReferenceService(
        connect_factory=Mock(), socket_factory=lambda *args, **kwargs: ws)
    service.publish = Mock()
    with caplog.at_level("WARNING", logger="kraken-reference"):
        with pytest.raises(BookIntegrityError, match="stale_book"):
            service._session(Mock())

    observed = [v.message for v in caplog.records
                if "KRAKEN STALE BOOK DIAGNOSTIC | " in v.message]
    assert len(observed) == 1
    parsed = json.loads(observed[0].split(" | ", 1)[1])
    assert parsed["expired"] == [
        {"symbol": symbol, "reason": "EXCHANGE_TIMESTAMP_EXPIRED",
         "exchange_age_seconds": 11.0, "receipt_age_seconds": 11.0}
        for symbol in ("BTC-USD", "ETH-USD")]
    assert parsed["execution_authority"] == "NONE"
    assert parsed["broker_submission"] == "NONE"
    assert parsed["subscribed_market_count"] == 2
    assert "100.0" not in observed[0]  # never report book prices
    assert service.state.books == {}
    assert not service.state.connected
    assert ws.close.called


def test_report_does_not_promote_stale_quotes_or_set_execution_authority():
    state = ready_two()
    expired = runtime.stale_book_source_diagnostic(
        state, NOW + timedelta(seconds=12))
    assert {r["symbol"] for r in expired} == {"BTC-USD", "ETH-USD"}
    assert all(r["reason"] == "EXCHANGE_TIMESTAMP_EXPIRED" for r in expired)
    assert all(state.quote(exchange, NOW + timedelta(seconds=12)) is None
               for exchange in state.books)
    assert state.health(NOW + timedelta(seconds=12))["quotes"] == {}
