"""Mocked wire-to-shared-reader delivery; no network or database connection."""
from contextlib import contextmanager
from datetime import timedelta
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import coinbase_paper_pricing as pricing
import kraken_reference_runtime as runtime
from kraken_research_data import ResearchUnavailable
from test_kraken_reference_book import NOW, ack, message


def replay(monkeypatch, seconds, *, blocked_write_seconds=0):
    """Run the actual ingestion/publisher with 200 checksum-valid updates/sec."""
    import database

    clock = {'value': 0.0}
    row = {}
    writes = []
    delayed = False
    shared = {}
    service = runtime.KrakenReferenceService(connect_factory=Mock())
    conn = Mock()

    def execute(sql, params=None):
        nonlocal delayed
        if 'INSERT INTO kraken_reference_state' in sql:
            published, encoded = params
            payload = json.loads(encoded)
            if payload['quotes'] and blocked_write_seconds and not delayed:
                delayed = True
                clock['value'] += blocked_write_seconds
            row.update(updated_at=published, payload=payload)
            writes.append((clock['value'], published, payload))
        return SimpleNamespace(fetchone=lambda: dict(row) if row else None)

    conn.execute.side_effect = execute

    @contextmanager
    def connect():
        yield conn

    monkeypatch.setattr(database, 'connect', connect)
    monkeypatch.setattr(runtime, 'utc_now', lambda: NOW + timedelta(seconds=clock['value']))
    monkeypatch.setattr(runtime.time, 'monotonic', lambda: clock['value'])

    def events():
        for symbol in runtime.SYMBOLS:
            yield 0.0, ack(symbol)
            yield 0.0, message(symbol=symbol)
        for tick in range(1, seconds * 100 + 1):
            elapsed = tick / 100
            for symbol in runtime.SYMBOLS:
                yield elapsed, message(symbol=symbol, kind='update', when=NOW + timedelta(seconds=elapsed))

    messages = iter(events())

    def receive():
        try:
            elapsed, raw = next(messages)
            clock['value'] = max(clock['value'], elapsed)
            return raw
        except StopIteration:
            shared.update(runtime.read_kraken_reference_state())
            service.stop_event.set()
            return '{"channel":"heartbeat"}'

    socket = Mock()
    socket.recv.side_effect = receive
    service.socket_factory = lambda *a, **kw: socket
    service._session(conn)
    return shared, writes, service


@pytest.mark.parametrize('seconds', [3, 8])
def test_current_coinbase_book_aligns_with_shared_kraken_delivery(monkeypatch, seconds):
    # At t3 the old publisher still exposes its initial empty snapshot; at t8
    # its last publication is t5, outside the unchanged two-second alignment.
    shared, writes, service = replay(monkeypatch, seconds)
    current = NOW + timedelta(seconds=seconds)
    reference = shared['quotes'].get('BTC-USD')
    book = {'time': current.isoformat(), 'sequence': 3, 'auction_mode': False,
            'bids': [['100', '2', 1]], 'asks': [['101', '2', 1]]}
    quote = pricing.validate_book('BTC-USD', book, current, reference, current)
    assert quote['independent_comparison']['status'] == 'ALIGNED_VENUE_COMPARISON'
    assert reference['quote_timestamp'] == reference['received_at'] == current.isoformat()
    assert reference['checksum_status'] == 'VALID'
    assert reference['execution_authority'] == 'NONE'
    assert reference['reference_only'] is True and reference['execution_quote_eligible'] is False
    assert service.state.counts['checksum_failures'] == 0
    assert quote['execution_authority'] == 'PAPER_SIMULATION_ONLY'


@pytest.mark.parametrize('blocked_write_seconds', [0, 3])
def test_dense_updates_coalesce_without_post_write_catchup(monkeypatch, blocked_write_seconds):
    shared, writes, service = replay(monkeypatch, 8, blocked_write_seconds=blocked_write_seconds)
    periodic = [(completed, published, payload) for completed, published, payload in writes if payload['quotes']]
    assert service.state.counts['updates'] == 1600
    assert 1 < len(periodic) <= 8
    # Count actual completed writes, including a simulated synchronous DB stall.
    assert all(later[0] - earlier[0] >= 1 for earlier, later in zip(periodic, periodic[1:]))
    assert len(writes) == len(periodic) + 1  # One initial lifecycle invalidation.
    assert shared['quotes']['BTC-USD']['execution_authority'] == 'NONE'


def test_delivered_snapshot_still_expires_without_refreshing_source_clocks(monkeypatch):
    shared, writes, _ = replay(monkeypatch, 3)
    _, published, payload = writes[-1]
    original = payload['quotes']['BTC-USD']['quote_timestamp']
    at_limit = runtime.display_state(payload, published, published + timedelta(seconds=10))
    assert at_limit['quotes']['BTC-USD']['quote_timestamp'] == original
    assert at_limit['quotes']['BTC-USD']['received_at'] == original
    expired = runtime.display_state(payload, published, published + timedelta(seconds=10, microseconds=1))
    assert expired['status'] == 'PUBLISHER_STALE' and not expired['quotes']
    later = published + timedelta(seconds=11)
    assert not runtime.display_state(payload, later, later)['quotes']
    # A still-fresh book must also satisfy the original cross-venue time bound.
    current = published + timedelta(seconds=3)
    book = {'time': current.isoformat(), 'sequence': 3, 'auction_mode': False,
            'bids': [['100', '2', 1]], 'asks': [['101', '2', 1]]}
    with pytest.raises(ResearchUnavailable, match='INDEPENDENT_BOOK_TIMESTAMPS_UNALIGNED'):
        pricing.validate_book('BTC-USD', book, current, shared['quotes']['BTC-USD'], current)
