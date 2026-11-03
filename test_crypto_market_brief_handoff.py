"""Paused cash scanning must not require crypto to start stock research for its brief."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import market_worker as worker


def configure(monkeypatch):
    for name, value in {
        'ENABLE_CRYPTO_MARKET_BRIEF_HANDOFF': 'true',
        'ENABLE_CASH_MARKET_RESEARCH': 'false',
        'MARKET_BRIEF_GITHUB_ISSUE': '338',
        'EXECUTION_MODE': 'paper',
        'ENABLE_BROKER_SUBMISSION': 'false',
        'LIVE_TRADING_ARMED': 'false',
    }.items():
        monkeypatch.setenv(name, value)


def cycle(monkeypatch):
    events = []
    class Executor:
        def __init__(self, **kwargs): pass
        def submit(self, function, *args):
            events.append((function.__name__, args))
            return SimpleNamespace(done=lambda:False)
        def shutdown(self, **kwargs): pass
    monkeypatch.setattr(worker,'ThreadPoolExecutor',Executor)
    monkeypatch.setattr(worker,'_bootstrap_worker_database',lambda *_:True)
    monkeypatch.setattr(worker,'cadence_for',lambda *_:SimpleNamespace(pulse_seconds=1,fast_scan_seconds=20,deep_scan_seconds=60,session_label='test'))
    monkeypatch.setattr(worker,'set_market_status',lambda *a,**k:None)
    monkeypatch.setattr(worker,'_run_scheduled_database_maintenance',lambda *_:None)
    monkeypatch.setattr(worker,'_cash_opportunity_scans_enabled',lambda _:False)
    monkeypatch.setattr(worker,'ALWAYS_ON_TRADING',True)
    monkeypatch.setattr(worker,'FAST_SIGNAL_SCAN_ENABLED',True)
    monkeypatch.setattr(worker,'REALTIME_MODE',False)
    monkeypatch.setattr(worker,'stop_event',SimpleNamespace(is_set=lambda:False,wait=lambda _:True))
    worker.run_worker('crypto')
    return events


def test_crypto_cycle_schedules_only_brief_when_cash_standby(monkeypatch):
    configure(monkeypatch)
    events = cycle(monkeypatch)
    assert ('_collect_market_brief_intelligence', ()) in events
    assert ('_run_brain_learning_sync', ('crypto',)) in events
    assert all(name != '_collect_stock_intelligence' for name, _ in events)
    assert all(not args or args == ('crypto',) for _, args in events)


@pytest.mark.parametrize('name,value',[
    ('ENABLE_CRYPTO_MARKET_BRIEF_HANDOFF',None),
    ('ENABLE_CRYPTO_MARKET_BRIEF_HANDOFF','false'),
    ('ENABLE_CASH_MARKET_RESEARCH','true'),
    ('MARKET_BRIEF_GITHUB_ISSUE',''),
    ('EXECUTION_MODE',None),('EXECUTION_MODE','live'),
    ('ENABLE_BROKER_SUBMISSION',None),('ENABLE_BROKER_SUBMISSION','true'),
    ('LIVE_TRADING_ARMED',None),('LIVE_TRADING_ARMED','true'),
])
def test_handoff_never_schedules_without_explicit_context(monkeypatch,name,value):
    configure(monkeypatch)
    if value is None: monkeypatch.delenv(name)
    else: monkeypatch.setenv(name,value)
    assert all(label != '_collect_market_brief_intelligence' for label,_ in cycle(monkeypatch))


def test_crypto_brief_helper_never_calls_broad_stock_collectors(monkeypatch):
    brief = Mock(return_value={'status':'ingested','brief_id':'2026-11-03','events_persisted':1})
    broad = Mock(side_effect=AssertionError('crypto brief cannot start stock collectors'))
    monkeypatch.setattr(worker,'sync_market_brief_inbox',brief)
    monkeypatch.setattr(worker,'collect_all',broad)
    worker._collect_market_brief_intelligence()
    brief.assert_called_once_with()
    broad.assert_not_called()


@pytest.mark.parametrize('status',[401,403,429])
def test_brief_http_failure_retains_exact_diagnostics_without_records(monkeypatch,caplog,status):
    from urllib.error import HTTPError
    from email.message import Message
    import github_market_intelligence_inbox as inbox
    headers=Message()
    headers['X-RateLimit-Remaining']='0' if status==429 else '10'
    headers['X-GitHub-Request-Id']='handoff-fixture'
    error=inbox._github_http_error(HTTPError('https://api.github.com/repos/example/issue',status,'failure',headers,None))
    monkeypatch.setattr(worker,'sync_market_brief_inbox',Mock(side_effect=error))
    broad=Mock(side_effect=AssertionError('No broad collectors in crypto handoff'))
    records=Mock(side_effect=AssertionError('No fabricated intake on HTTP failure'))
    monkeypatch.setattr(worker,'collect_all',broad)
    monkeypatch.setattr(worker,'ingest_monitor_record',records)
    with caplog.at_level('WARNING',logger='market-worker'):
        worker._collect_market_brief_intelligence()
    assert f'http_status={status}' in caplog.text
    assert f"failure_class={error.diagnostics['failure_class']}" in caplog.text
    assert 'execution_impact=NONE' in caplog.text
    broad.assert_not_called()
    records.assert_not_called()
