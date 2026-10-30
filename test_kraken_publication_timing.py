"""Monotonic publication attribution with mocked clocks/storage/log sinks."""
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import kraken_reference_runtime as runtime
from test_kraken_reference_book import NOW, ready


PHASES = ('state_capture', 'history_lock_wait', 'history_copy', 'comparisons',
          'write_state', 'health_logging')
PREFIX = 'KRAKEN PUBLICATION TIMING | %s'


def setup_publication(monkeypatch, failure_stage=None, timing_log_error=False):
    clock = SimpleNamespace(value=100.0)
    monkeypatch.setattr(runtime.time, 'monotonic', lambda: clock.value)
    monkeypatch.setattr(runtime, 'utc_now', lambda: NOW)
    service = runtime.KrakenReferenceService(connect_factory=Mock())
    service.state = ready()
    original = RuntimeError('private-credential-and-SQL-text-must-not-enter-diagnostic')
    emitted = []
    timing_attempts = []
    stored = []
    real_health = service.state.health

    def health(now):
        clock.value += .1
        if failure_stage == 'state_capture':
            raise original
        return real_health(now)

    service.state.health = health

    class HistoryLock:
        released = False

        def __enter__(self):
            clock.value += .2
            if failure_stage == 'history_lock_wait':
                raise original

        def __exit__(self, *args):
            self.released = True

    service.lock = HistoryLock()

    class PrimaryCopy:
        def keys(self):
            clock.value += .3
            if failure_stage == 'history_copy':
                raise original
            return []

    service.primary = PrimaryCopy()
    compare = runtime.compare_reference

    def comparison(*args):
        clock.value += .25
        if failure_stage == 'comparisons':
            raise original
        return compare(*args)

    monkeypatch.setattr(runtime, 'compare_reference', comparison)
    conn = Mock()

    def execute(statement, params):
        clock.value += .7
        if failure_stage == 'write_state':
            raise original
        stored.append((statement, params))

    conn.execute.side_effect = execute

    def log_info(format_string, encoded):
        if format_string == PREFIX:
            timing_attempts.append(encoded)
            if timing_log_error:
                raise RuntimeError('diagnostic sink unavailable')
            emitted.append(json.loads(encoded))
        else:
            clock.value += .8
            if failure_stage == 'health_logging':
                raise original

    monkeypatch.setattr(runtime.log, 'info', log_info)
    return service, conn, clock, emitted, timing_attempts, stored, original


def test_publish_attributes_local_wall_phases_without_changing_payload(monkeypatch):
    service, conn, clock, records, _, stored, _ = setup_publication(monkeypatch)
    expected = ready().health(NOW)
    # Session identity belongs to the actual producer, not a second fixture.
    for quote in expected['quotes'].values():
        quote['session_id'] = service.state.session_id
    expected['subscribed_symbols'] = list(runtime.SYMBOLS.values())
    expected['comparisons'] = {symbol: {
        'status': 'UNAVAILABLE_OR_UNALIGNED', 'reason': 'PRIMARY_NOT_OBSERVED',
        'execution_impact': 'NONE', 'checked_at': NOW.isoformat(),
    } for symbol in runtime.SYMBOLS.values()}
    service.publish(conn)
    assert len(records) == 1
    timing = records[0]
    assert set(timing) == {'status', 'failure_stage', 'phase_ms', 'total_ms', 'scope'}
    assert timing['status'] == 'SUCCESS' and timing['failure_stage'] is None
    assert timing['phase_ms'] == pytest.approx(dict(zip(PHASES, [100, 200, 300, 500, 700, 800])))
    assert timing['total_ms'] == pytest.approx(2600)
    assert timing['scope'] == 'publication_before_timing_log'
    conn.execute.assert_called_once()
    _, params = stored[0]
    assert params[0] == NOW
    assert json.loads(params[1]) == expected
    assert service.last_log == pytest.approx(101.8)
    assert service.lock.released
    assert not any(key in json.dumps(timing) for key in ('private-credential', 'SELECT', 'INSERT', 'bid', 'ask', 'provider', 'authority'))


@pytest.mark.parametrize('stage', PHASES)
def test_failure_stage_preserves_original_error_and_unexecuted_phases(monkeypatch, stage):
    service, conn, _, records, _, _, original = setup_publication(monkeypatch, failure_stage=stage)
    with pytest.raises(RuntimeError) as caught:
        service.publish(conn)
    assert caught.value is original
    assert len(records) == 1
    timing = records[0]
    assert timing['status'] == 'FAILED' and timing['failure_stage'] == stage
    for phase in PHASES[:PHASES.index(stage)+1]:
        assert timing['phase_ms'][phase] is not None
    for phase in PHASES[PHASES.index(stage)+1:]:
        assert timing['phase_ms'][phase] is None
    assert 'private-credential' not in json.dumps(timing)
    if PHASES.index(stage) >= PHASES.index('history_copy'):
        assert service.lock.released
    assert conn.execute.call_count == (1 if stage in {'write_state', 'health_logging'} else 0)


def test_skipped_health_log_is_unmeasured_and_timing_logs_are_throttled(monkeypatch):
    service, conn, clock, records, _, _, _ = setup_publication(monkeypatch)
    service.publish(conn)
    first_finished = clock.value
    clock.value = first_finished + 1
    service.publish(conn)
    assert len(records) == 1
    # The throttle measures completed publication wall time, not invocation count.
    clock.value = first_finished + 30 - 1.8 - .001
    service.last_log = clock.value  # Independently not yet due for health logging.
    service.publish(conn)
    assert len(records) == 1
    clock.value = first_finished + 30 - 1.8 + .001
    service.publish(conn)
    assert len(records) == 2
    assert records[-1]['phase_ms']['health_logging'] is None
    assert conn.execute.call_count == 4


def test_timing_sink_failure_is_neutral_and_consumes_logging_interval(monkeypatch):
    service, conn, clock, _, attempts, _, _ = setup_publication(monkeypatch, timing_log_error=True)
    service.publish(conn)
    clock.value += 1
    service.publish(conn)
    assert len(attempts) == 1
    assert conn.execute.call_count == 2


def test_timing_sink_failure_cannot_replace_original_storage_error(monkeypatch):
    service, conn, _, _, attempts, _, original = setup_publication(
        monkeypatch, failure_stage='write_state', timing_log_error=True)
    with pytest.raises(RuntimeError) as caught:
        service.publish(conn)
    assert caught.value is original
    assert len(attempts) == 1


def test_unavailable_diagnostic_clock_cannot_fail_successful_storage(monkeypatch):
    service, conn, clock, _, _, _, _ = setup_publication(monkeypatch)
    calls = 0

    def intermittent_clock():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError('diagnostic-only initial clock failure')
        return clock.value

    monkeypatch.setattr(runtime.time, 'monotonic', intermittent_clock)
    service.publish(conn)
    conn.execute.assert_called_once()


def test_final_diagnostic_clock_failure_cannot_replace_storage_error(monkeypatch):
    service, conn, clock, records, _, _, original = setup_publication(monkeypatch)
    storage_failed = False

    def monotonic():
        if storage_failed:
            raise RuntimeError('clock unavailable after original storage failure')
        return clock.value

    def execute(*args):
        nonlocal storage_failed
        storage_failed = True
        raise original

    monkeypatch.setattr(runtime.time, 'monotonic', monotonic)
    conn.execute.side_effect = execute
    with pytest.raises(RuntimeError) as caught:
        service.publish(conn)
    assert caught.value is original and not records
    conn.execute.assert_called_once()


def test_twenty_two_second_write_stall_cannot_renew_reference_timestamps(monkeypatch):
    service, conn, clock, records, _, stored, _ = setup_publication(monkeypatch)
    capture_clock = Mock(return_value=NOW)
    monkeypatch.setattr(runtime, 'utc_now', capture_clock)
    started = clock.value

    def stalled_write(statement, params):
        clock.value += 22
        stored.append((statement, params))

    conn.execute.side_effect = stalled_write
    service.publish(conn)
    capture_clock.assert_called_once_with()
    conn.execute.assert_called_once()
    published_at, encoded = stored[0][1]
    payload = json.loads(encoded)
    original_quote = payload['quotes']['BTC-USD']
    assert published_at == NOW
    assert original_quote['quote_timestamp'] == original_quote['received_at'] == NOW.isoformat()
    assert original_quote['execution_authority'] == 'NONE'
    assert original_quote['reference_only'] is True
    assert original_quote['execution_quote_eligible'] is False
    assert records[0]['phase_ms']['write_state'] == pytest.approx(22000)
    assert records[0]['total_ms'] == pytest.approx((clock.value-started)*1000)
    shown = runtime.display_state(payload, published_at, NOW+timedelta(seconds=clock.value-started))
    assert shown['status'] == 'PUBLISHER_STALE' and shown['connected'] is False
    assert shown['quotes'] == {} and shown['comparisons'] == {}
    assert shown['execution_authority'] == 'NONE'
