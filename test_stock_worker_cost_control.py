"""Cash opportunity cost control must preserve the running research worker."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
import market_worker as worker


@pytest.mark.parametrize('market,value', [('crypto','false'),('cash',None),('cash','true'),('cash','invalid'),('cash','')])
def test_default_unknown_and_crypto_keep_existing_scans(monkeypatch, market, value):
    if value is None:
        monkeypatch.delenv('ENABLE_CASH_MARKET_RESEARCH', raising=False)
    else:
        monkeypatch.setenv('ENABLE_CASH_MARKET_RESEARCH', value)
    monkeypatch.setattr(worker, 'connect', lambda: pytest.fail('Unexpected inventory read'))
    assert worker._cash_opportunity_scans_enabled(market)


@pytest.mark.parametrize('value', ['false','0','no','off',' FALSE '])
@pytest.mark.parametrize('outstanding', [True, False])
def test_explicit_pause_requires_verified_empty_positions_and_orders(monkeypatch, value, outstanding):
    monkeypatch.setenv('ENABLE_CASH_MARKET_RESEARCH', value)
    @contextmanager
    def connect():
        def execute(sql, params):
            assert 'FROM positions WHERE market=%s' in sql
            assert 'FROM paper_orders WHERE market=%s' in sql
            assert 'quantity IS NULL OR quantity <> 0' in sql
            assert 'status IS NULL OR status NOT IN' in sql
            assert params == ('cash', 'cash')
            return SimpleNamespace(fetchone=lambda: {'outstanding':outstanding})
        yield SimpleNamespace(execute=execute)
    monkeypatch.setattr(worker, 'connect', connect)
    assert worker._cash_opportunity_scans_enabled('cash') is outstanding


@pytest.mark.parametrize('result', [None, {}, {'outstanding':None}, {'outstanding':'false'}])
def test_incomplete_inventory_retains_monitoring(monkeypatch, result):
    monkeypatch.setenv('ENABLE_CASH_MARKET_RESEARCH','false')
    @contextmanager
    def connect():
        yield SimpleNamespace(execute=lambda *args: SimpleNamespace(fetchone=lambda:result))
    monkeypatch.setattr(worker, 'connect', connect)
    assert worker._cash_opportunity_scans_enabled('cash')


def test_inventory_failure_retains_monitoring(monkeypatch):
    monkeypatch.setenv('ENABLE_CASH_MARKET_RESEARCH','false')
    def connect():
        raise RuntimeError('test database unavailable')
    monkeypatch.setattr(worker,'connect',connect)
    assert worker._cash_opportunity_scans_enabled('cash')


@pytest.mark.parametrize('scans_enabled',[False,True])
def test_worker_cycle_preserves_intelligence_brain_and_positions(monkeypatch, scans_enabled):
    events=[]
    class Executor:
        def __init__(self, **kwargs): pass
        def submit(self, function, *args):
            events.append((function.__name__,args))
            return SimpleNamespace(done=lambda:False)
        def shutdown(self, **kwargs): pass
    monkeypatch.setattr(worker,'ThreadPoolExecutor',Executor)
    monkeypatch.setattr(worker,'_bootstrap_worker_database',lambda *args:True)
    import alpaca_auth_recovery
    monkeypatch.setattr(alpaca_auth_recovery,'recover_alpaca_authentication',lambda:None)
    monkeypatch.setattr(worker,'cadence_for',lambda *args:SimpleNamespace(pulse_seconds=1, fast_scan_seconds=20,deep_scan_seconds=60,session_label='test'))
    monkeypatch.setattr(worker,'set_market_status',lambda *args,**kwargs:None)
    monkeypatch.setattr(worker,'_run_scheduled_database_maintenance',lambda *args:None)
    monkeypatch.setattr(worker,'_cash_opportunity_scans_enabled',lambda market:scans_enabled)
    monkeypatch.setattr(worker,'ALWAYS_ON_TRADING',True)
    monkeypatch.setattr(worker,'FAST_SIGNAL_SCAN_ENABLED',True)
    monkeypatch.setattr(worker,'REALTIME_MODE',True)
    monkeypatch.setattr(worker,'stop_event',SimpleNamespace(is_set=lambda:False,wait=lambda _:True))
    def pulse(market):
        events.append(('position_pulse',(market,)))
        return [],0,'unavailable'
    monkeypatch.setattr(worker,'live_position_pulse',pulse)
    worker.run_worker('cash')
    names=[name for name,args in events]
    assert '_collect_stock_intelligence' in names
    assert '_run_brain_learning_sync' in names
    assert 'position_pulse' in names
    assert ('scan_market' in names) is scans_enabled
    assert ('fast_scan_market' in names) is scans_enabled
    assert all(args==('cash',) for name,args in events if name!='_collect_stock_intelligence')


@pytest.mark.skipif(not worker.os.getenv('DATABASE_URL'), reason='Disposable PostgreSQL required')
@pytest.mark.parametrize('quantity,status,expected', [
    (0,'FILLED',False), (1,'FILLED',True), (-1,'FILLED',True),
    (None,'FILLED',True), (0,'NEW',True), (0,None,True),
    (0,'unknown',True), (0,'PARTIAL_CANCELLED',False),
])
def test_postgres_inventory_pause_policy(monkeypatch, quantity,status,expected):
    """Temporary tables isolate this query from canonical trading records."""
    from database import connect as real_connect
    monkeypatch.setenv('ENABLE_CASH_MARKET_RESEARCH','false')
    with real_connect() as conn:
        conn.execute('CREATE TEMP TABLE positions(market TEXT,quantity DOUBLE PRECISION) ON COMMIT DROP')
        conn.execute('CREATE TEMP TABLE paper_orders(market TEXT,status TEXT) ON COMMIT DROP')
        conn.execute('INSERT INTO positions VALUES (%s,%s)',('cash',quantity))
        conn.execute('INSERT INTO positions VALUES (%s,%s)',('crypto',10))
        conn.execute('INSERT INTO paper_orders VALUES (%s,%s)',('cash',status))
        conn.execute('INSERT INTO paper_orders VALUES (%s,%s)',('crypto','NEW'))
        @contextmanager
        def connect():
            yield conn
        monkeypatch.setattr(worker,'connect',connect)
        assert worker._cash_opportunity_scans_enabled('cash') is expected
