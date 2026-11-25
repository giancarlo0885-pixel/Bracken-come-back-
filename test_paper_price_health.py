"""Event-driven diagnostic tests use synthetic snapshots, never real quotes/orders."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
import json
import threading
from unittest.mock import Mock

import database
import paper_price_health as health
from test_kraken_reference_book import NOW


def snapshot(symbol='BTC-USD', at=NOW):
    return {symbol: {'status': 'VALID_PAPER_PRICE', 'quote_timestamp': at.isoformat(),
                     'quote_id': 'original-' + symbol, 'execution_authority': 'PAPER_SIMULATION_ONLY'}}


def sink(clock=lambda: NOW, publish=True):
    return health.HealthPublication('synthetic paper source', clock, publish, Mock(), 'PAPER PRICE HEALTH')


def test_blocked_commit_cannot_block_submit_and_latest_snapshot_is_immutable(monkeypatch):
    entered, release, second = threading.Event(), threading.Event(), threading.Event()
    writes = []
    class Cursor:
        def execute(self, sql, params):
            writes.append(json.loads(params[0]))
            if len(writes) == 2:
                second.set()
    @contextmanager
    def connection():
        yield Cursor()
        if len(writes) == 1:
            entered.set()
            assert release.wait(3)
    monkeypatch.setattr(database, 'connect', connection)
    publisher = health.LatestHealthPublisher(sink(), threading.Event())
    try:
        assert publisher.start()
        assert not publisher.start()
        assert publisher.thread.daemon
        publisher.submit(snapshot())
        assert entered.wait(2)
        publisher.submit(snapshot('ETH-USD'))
        last = snapshot('ADA-USD')
        assert publisher.submit(last)
        last['ADA-USD']['quote_timestamp'] = 'mutated after enqueue'
        with publisher._lock:
            assert list(publisher._pending) == ['ADA-USD']
        release.set()
        assert second.wait(2)
    finally:
        release.set()
        assert publisher.stop()
    assert [list(write['symbols']) for write in writes] == [['BTC-USD'], ['ADA-USD']]
    assert writes[-1]['symbols']['ADA-USD']['quote_timestamp'] == NOW.isoformat()


def test_pending_symbols_are_bounded_and_source_snapshot_never_mutated(monkeypatch):
    original = {str(i): next(iter(snapshot().values())) for i in range(20)}
    before = deepcopy(original)
    publisher = health.LatestHealthPublisher(sink(publish=False), threading.Event())
    assert publisher.submit(original)
    assert len(publisher._pending) == 8
    assert list(publisher._pending) == [str(i) for i in range(12, 20)]
    publisher.sink.clock = lambda: NOW + timedelta(seconds=6)
    done = threading.Event()
    publisher.sink.logger.info.side_effect = lambda *a, **k: done.set()
    try:
        assert publisher.start()
        assert done.wait(2)
    finally:
        assert publisher.stop()
    assert original == before
    assert all(v['status'] == 'STALE_PAPER_PRICE' and v['execution_authority'] == 'NONE'
               for v in publisher.sink.last_health.values())


def test_failure_retries_without_another_quote_and_does_not_acknowledge_commit(monkeypatch):
    ticks = [0.0]
    monkeypatch.setattr(health.time, 'monotonic', lambda: ticks[0])
    first, retried = threading.Event(), threading.Event()
    attempts = []
    class Cursor:
        def execute(self, sql, params): pass
    @contextmanager
    def connection():
        attempts.append(ticks[0])
        yield Cursor()
        if len(attempts) == 1:
            raise RuntimeError('private connection secret')
        retried.set()
    monkeypatch.setattr(database, 'connect', connection)
    target = sink()
    target.logger.info.side_effect = lambda *args: first.set() if 'UNAVAILABLE' in args[2] else None
    publisher = health.LatestHealthPublisher(target, threading.Event())
    try:
        publisher.start()
        publisher.submit({'BTC-USD': {'status': 'UNAVAILABLE', 'reason': 'NO_FRESH_BOOK'}})
        assert first.wait(2)
        # The acknowledgement is never set on an execute/commit failure.
        assert target._health_publication_signature is None
        ticks[0] = 4
        publisher._event.set()
        assert not retried.wait(.05)
        ticks[0] = 5
        publisher._event.set()
        assert retried.wait(2)
    finally:
        publisher.stop()
    assert attempts == [0, 5]
    assert 'private' not in repr(target.logger.mock_calls)


def test_logs_recheck_expiry_after_slow_commit_without_redating_source(monkeypatch):
    clock = [NOW]
    class Cursor:
        def execute(self, sql, params):
            assert json.loads(params[0])['symbols']['BTC-USD']['quote_timestamp'] == NOW.isoformat()
    @contextmanager
    def connection():
        yield Cursor()
        clock[0] += timedelta(seconds=6)
    monkeypatch.setattr(database, 'connect', connection)
    target = sink(lambda: clock[0])
    target.last_health = snapshot()
    assert target._publish_health() == 'COMMITTED'
    assert target.last_health['BTC-USD']['status'] == 'STALE_PAPER_PRICE'
    assert target.last_health['BTC-USD']['quote_timestamp'] == NOW.isoformat()
    logged = json.loads(target.logger.info.call_args.args[2])
    assert logged['symbols']['BTC-USD']['execution_authority'] == 'NONE'
    assert target._health_publication_monotonic <= target._health_attempt_monotonic


def test_return_to_already_committed_state_cancels_failed_write_retry(monkeypatch):
    ticks = [0.0]
    monkeypatch.setattr(health.time, 'monotonic', lambda: ticks[0])
    failed, suppressed = threading.Event(), threading.Event()
    writes = []
    class Cursor:
        def execute(self, sql, params): writes.append(params[0])
    @contextmanager
    def connection():
        yield Cursor()
        if len(writes) == 2:
            raise RuntimeError('synthetic failure')
    monkeypatch.setattr(database, 'connect', connection)
    target = sink()
    original = {'BTC-USD': {'status': 'UNAVAILABLE', 'reason': 'NO_FRESH_BOOK'}}
    target.last_health = deepcopy(original)
    assert target._publish_health() == 'COMMITTED'
    target.logger.info.side_effect = lambda *args: failed.set() if '"UNAVAILABLE"' in args[2] and 'synthetic changed state' in args[2] else None
    publish = target._publish_health
    statuses = []
    def observe():
        status = publish()
        statuses.append(status)
        if status is None:
            suppressed.set()
        return status
    target._publish_health = observe
    publisher = health.LatestHealthPublisher(target, threading.Event())
    try:
        ticks[0] = 1
        publisher.start()
        publisher.submit({'BTC-USD': {'status': 'UNAVAILABLE', 'reason': 'synthetic changed state'}})
        assert failed.wait(2)
        publisher.submit(original)
        assert suppressed.wait(2)
        ticks[0] = 10
        publisher._event.set()
        assert not publisher.stop_event.wait(.05)
    finally:
        publisher.stop()
    assert statuses == ['UNAVAILABLE', None]
    assert len(writes) == 2


def test_stop_is_responsive_idle_and_submissions_after_stop_are_rejected():
    target = sink(publish=False)
    stop = threading.Event()
    publisher = health.LatestHealthPublisher(target, stop)
    assert publisher.start()
    assert publisher.stop()
    assert stop.is_set()
    assert not publisher.submit(snapshot())
    assert not publisher.start()
    target.logger.info.assert_not_called()
