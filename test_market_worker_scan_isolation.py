"""Slow analysis futures must not stall scheduler dispatch or queue duplicate scans.

Execution-lock and provider/DB latency are separate from this scheduler boundary.
"""
import threading
from types import SimpleNamespace
import market_worker as worker


def test_pending_fast_and_deep_analysis_keep_pulse_dispatch_running(monkeypatch):
    entered={name:threading.Event() for name in ('fast','deep')}
    exited={name:threading.Event() for name in entered}
    release=threading.Event()
    launches=[];pulses=[];clock=[0.0]
    def scan(name):
        def run(market):
            launches.append((name,market));entered[name].set()
            try:
                assert release.wait(10), 'fixture did not release pending scan'
                return []
            finally:exited[name].set()
        return run
    def pulse(market):
        assert all(event.wait(3) for event in entered.values())
        assert not release.is_set()
        pulses.append(market)
        return [],0,'fixture'
    def wait(seconds):
        clock[0]+=seconds
        return len(pulses)>=3
    monkeypatch.setattr(worker,'stop_event',SimpleNamespace(is_set=lambda:False,wait=wait))
    monkeypatch.setattr(worker,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    monkeypatch.setattr(worker,'_bootstrap_worker_database',lambda _:True)
    monkeypatch.setattr(worker,'cadence_for',lambda _:SimpleNamespace(pulse_seconds=10,fast_scan_seconds=20,deep_scan_seconds=90,session_label='24/7'))
    monkeypatch.setattr(worker,'_run_scheduled_database_maintenance',lambda _:None)
    monkeypatch.setattr(worker,'set_market_status',lambda *a,**k:None)
    monkeypatch.setattr(worker,'_run_brain_learning_sync',lambda _: {})
    monkeypatch.setattr(worker,'_cash_opportunity_scans_enabled',lambda _:True)
    monkeypatch.setattr(worker,'scan_market',scan('deep'))
    monkeypatch.setattr(worker,'fast_scan_market',scan('fast'))
    monkeypatch.setattr(worker,'live_position_pulse',pulse)
    monkeypatch.setattr(worker,'ALWAYS_ON_TRADING',True)
    monkeypatch.setattr(worker,'FAST_SIGNAL_SCAN_ENABLED',True)
    monkeypatch.setattr(worker,'REALTIME_MODE',True)
    try:
        worker.run_worker('crypto')
        assert pulses==['crypto']*3
        assert sorted(launches)==[('deep','crypto'),('fast','crypto')]
    finally:
        release.set()
        for event in exited.values():assert event.wait(3)
