"""No-network Coinbase Level2 batch subscription and fail-closed lifecycle tests."""
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest
import websocket

import coinbase_l2_shadow_runtime as feed


NOW = datetime(2026, 10, 20, 18, 45, tzinfo=timezone.utc)


def packet(kind, symbol="BTC-USD", **kwargs):
    return json.dumps({"type": kind, "product_id": symbol, **kwargs})


def ack(symbol="BTC-USD"):
    return json.dumps({"type": "subscriptions", "channels": [{
        "name": "level2_batch", "product_ids": [symbol]}]})


def snap(symbol="BTC-USD"):
    return packet("snapshot", symbol, bids=[["99", "2"]], asks=[["101", "1"]])


def upd(symbol="BTC-USD", *, moment=NOW, changes=None):
    return packet("l2update", symbol, time=moment.isoformat(),
                  changes=changes or [["buy", "100", "3"]])


class Wire:
    def __init__(self, messages, on_recv=None):
        self.messages = list(messages)
        self.on_recv = on_recv
        self.outbound = []
        self.timeout = None
        self.closed = False

    def settimeout(self, n):
        self.timeout = n

    def send(self, message):
        self.outbound.append(json.loads(message))

    def recv(self):
        if self.on_recv:
            self.on_recv()
        if not self.messages:
            raise websocket.WebSocketTimeoutException("mock timeout")
        item = self.messages.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.closed = True


def setup_probe(messages, symbol="BTC-USD", clock=None, monotonic=None):
    ws = Wire(messages)
    opened = []

    def factory(uri, **kwargs):
        opened.append((uri, kwargs))
        return ws

    probe = feed.CoinbaseShadowWireProbe(
        symbol, socket_factory=factory,
        clock=clock or (lambda: NOW),
        monotonic=monotonic or (lambda: 0.0),
    )
    return probe, ws, opened


def test_unauthenticated_level2_batch_only_one_market_per_socket():
    request = feed.subscription("ETH-USD")
    assert request == {"type": "subscribe", "channels": [
        {"name": "level2_batch", "product_ids": ["ETH-USD"]}]}
    with pytest.raises(ValueError):
        feed.subscription("PAXG-USD")
    assert feed.subscribed(json.loads(ack("ETH-USD")), "ETH-USD")
    assert not feed.subscribed(json.loads(ack("ETH-USD")), "BTC-USD")
    assert not feed.subscribed({"type": "subscriptions", "channels": []}, "BTC-USD")


def test_receiving_snapshot_and_update_never_leaks_executable_quote():
    probe, ws, opened = setup_probe([ack(), snap(), upd()])
    captured = []

    def observe():
        quote = probe.books.shadow_reference("BTC-USD", now=NOW)
        captured.append(quote)

    ws.on_recv = observe
    probe.session(max_packets=3)
    assert len(opened) == 1
    assert opened[0][0] == feed.URI
    assert opened[0][1]["timeout"] == 2
    assert ws.timeout == 2
    assert ws.outbound == [feed.subscription("BTC-USD")]
    assert ws.closed
    assert probe.counts["snapshots"] == 1
    assert probe.counts["updates"] == 1
    assert probe.counts["wire_errors"] == 0
    assert captured[0] is None
    assert captured[1] is None
    assert captured[2] is None  # snapshot is not a timed executable quote
    assert probe.books.shadow_reference("BTC-USD", now=NOW) is None
    assert not hasattr(probe, "paper_execution_reality")


@pytest.mark.parametrize("bad,code", [
    ([snap()], "UNSUBSCRIBED_PRODUCT"),
    ([ack("ETH-USD")], "SUBSCRIPTION_UNCONFIRMED"),
    ([ack(), ack()], "DUPLICATE_SUBSCRIPTION"),
    ([ack(), packet("error", message="private rejected")], "SUBSCRIPTION_REJECTED"),
    ([ack(), snap("ETH-USD")], "UNSUBSCRIBED_PRODUCT"),
    ([ack(), upd()], "INVALID_LEVEL2_BOOK"),
    ([ack(), snap(), upd(moment=NOW - timedelta(seconds=90))], "INVALID_LEVEL2_BOOK"),
    ([ack(), snap(), upd(changes=[["buy", "102", "1"]])], "INVALID_LEVEL2_BOOK"),
    ([ack(), "[]"], "NON_OBJECT_FRAME"),
    ([ack(), "{broken"], "INVALID_JSON"),
    ([ack(), b"\xff"], "INVALID_UTF8"),
    ([ack(), "x" * (feed.MAX_WIRE_BYTES + 1)], "OVERSIZED_OR_INVALID_FRAME"),
    ([ack(), ""], "EMPTY_OR_CLOSED_FRAME"),
])
def test_wire_errors_force_disconnect_and_never_preserve_book(bad, code):
    probe, ws, _ = setup_probe(bad)
    with pytest.raises(feed.ShadowWireError) as err:
        probe.session(max_packets=100)
    assert str(err.value) == code
    assert ws.closed
    assert probe.books.shadow_reference("BTC-USD", now=NOW) is None


def test_l2_heartbeat_cannot_refresh_market_timestamp():
    probe, ws, _ = setup_probe([ack(), snap(), upd(), packet("heartbeat")])
    # First message after all packets: book is still invalid after cleanup.
    probe.session(max_packets=4)
    assert probe.books.shadow_reference("BTC-USD", now=NOW) is None


def test_wire_liveness_timeout_even_with_unsupported_messages():
    ticks = [0.0]

    def mono():
        ticks[0] += 1.0
        return ticks[0]

    probe, ws, _ = setup_probe([ack()] + [packet("ignored") for _ in range(20)],
                               monotonic=mono)
    with pytest.raises(feed.ShadowWireError, match="SNAPSHOT_TIMEOUT"):
        probe.session()
    assert ws.closed


def test_missing_subscription_ack_times_out_even_on_unsupported_packets():
    ticks = [0.0]

    def mono():
        ticks[0] += 1
        return ticks[0]

    probe, ws, _ = setup_probe([packet("ignored")] * 20, monotonic=mono)
    with pytest.raises(feed.ShadowWireError, match="SUBSCRIPTION_TIMEOUT"):
        probe.session()
    assert ws.closed


def test_stale_or_crossed_books_never_compared_to_kraken(monkeypatch):
    probe, ws, _ = setup_probe([ack(), snap()])
    probe.books.ingest({"type": "snapshot", "product_id": "BTC-USD",
                        "bids": [["99", "1"]], "asks": [["101", "1"]]},
                       received_at=NOW)
    assert probe._comparison(NOW)["status"] == "SOURCE_UNAVAILABLE"


def test_read_only_reference_alignment_and_expiry(monkeypatch):
    probe, ws, _ = setup_probe([])
    assert probe.books.ingest(json.loads(snap()), received_at=NOW)
    assert probe.books.ingest(json.loads(upd()), received_at=NOW)

    from kraken_reference_runtime import read_kraken_reference_state
    import kraken_reference_runtime as reference
    monkeypatch.setattr(reference, "read_kraken_reference_state", lambda: {
        "quotes": {"BTC-USD": {
            "symbol": "BTC-USD", "provider": "Kraken WS v2",
            "bid": "99", "ask": "101", "price": "100",
            "quote_timestamp": NOW.isoformat(), "received_at": NOW.isoformat(),
            "checksum_status": "VALID",
        }}
    })
    aligned = probe._comparison(NOW)
    assert aligned["status"] == "ALIGNED_VENUE_COMPARISON"
    assert aligned["mid_difference_bps"] == "0"
    assert probe.counts["comparisons"] == 1
    assert probe._comparison(NOW + timedelta(seconds=6))["status"] == "SOURCE_UNAVAILABLE"
    assert probe.counts["comparisons"] == 1


def test_enabling_requires_disarmed_paper_execution_and_never_changes_broker(monkeypatch):
    monkeypatch.setenv("COINBASE_L2_SHADOW_PROBE_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")
    started = []
    monkeypatch.setattr(feed.CoinbaseShadowWireProbe, "start",
                        lambda self: started.append(self.symbol) or True)
    worker = SimpleNamespace()
    assert feed.install_coinbase_shadow_probes(worker)
    assert sorted(started) == ["BTC-USD", "ETH-USD"]
    assert len(worker._coinbase_l2_shadow_probes) == 2
    assert not feed.install_coinbase_shadow_probes(worker)
    for key, bad in [("LIVE_TRADING_ARMED", "true"),
                     ("ENABLE_BROKER_SUBMISSION", "true"),
                     ("EXECUTION_MODE", "live")]:
        monkeypatch.setenv(key, bad)
        assert not feed.permitted()
        assert not feed.CoinbaseShadowWireProbe("BTC-USD").start()
        monkeypatch.setenv(key, "false" if key != "EXECUTION_MODE" else "paper")
    monkeypatch.delenv("COINBASE_L2_SHADOW_PROBE_ENABLED")
    assert not feed.permitted()


def test_backoff_is_bounded_and_does_not_use_tighter_than_two_seconds():
    assert [feed.reconnect_delay(x) for x in range(8)] == [1, 2, 4, 8, 16, 30, 30, 30]
