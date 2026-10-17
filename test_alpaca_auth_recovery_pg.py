"""Recovery writes only to isolated local PostgreSQL test schemas."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pandas as pd
import pytest

import alpaca_auth_recovery as recovery
from global_adaptive_engine import mark_provider_cooldown_db

NOW = datetime(2026, 10, 16, 5, tzinfo=timezone.utc)


def bars():
    frame = pd.DataFrame({'Open': [100.0], 'High': [102.0], 'Low': [99.0],
                          'Close': [101.0], 'Volume': [1000.0]},
                         index=pd.DatetimeIndex([NOW - timedelta(days=1)]))
    frame.attrs.update(provider='Alpaca IEX', requested_symbol='MSFT', provider_symbol='MSFT', feed='iex')
    return frame


@pytest.fixture
def db(monkeypatch):
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    from psycopg.conninfo import conninfo_to_dict
    dsn = os.getenv('DATABASE_URL', '')
    if not dsn:
        pytest.skip('PostgreSQL integration only')
    conf = conninfo_to_dict(dsn)
    if conf.get('host') not in {'localhost', '127.0.0.1', '::1'} or not conf.get('dbname', '').endswith('_test'):
        pytest.skip('Local disposable test database required')
    schema = 'alpaca_recovery_' + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    @contextmanager
    def connect():
        with psycopg.connect(dsn, options='-c search_path=' + schema + ',pg_catalog', row_factory=dict_row) as conn:
            yield conn
    for name, value in {'APCA_API_KEY_ID': 'unit-account-key', 'APCA_API_SECRET_KEY': 'unit-account-secret',
                        'ALPACA_DATA_URL': 'https://data.alpaca.markets', 'ALPACA_DATA_FEED': 'iex',
                        'EXECUTION_MODE': 'paper', 'ENABLE_BROKER_SUBMISSION': 'false', 'LIVE_TRADING_ARMED': 'false'}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(recovery, 'ALPACA_IEX_DAILY_REQUEST_BUDGET', 10)
    import provider_capabilities
    monkeypatch.setattr(provider_capabilities, 'capability_available', lambda *args: True)
    with connect() as conn:
        conn.execute('''CREATE TABLE provider_budget_ledger (
            provider TEXT,capability TEXT,utc_date TEXT,entitlement TEXT,daily_budget INT,
            requests_used INT,remaining_budget INT,latency_ms FLOAT8,last_success TEXT,
            last_failure TEXT,cooldown_until TEXT,data_mode TEXT,updated_at TEXT,
            PRIMARY KEY(provider,capability,utc_date))''')
    try:
        yield connect
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def seed(connect, *, same_day=True, reason=recovery.AUTH_REASON, recent=False, remaining=8):
    date = NOW.date() if same_day else (NOW - timedelta(days=1)).date()
    updated = NOW - timedelta(seconds=10) if recent else NOW - timedelta(hours=2 if same_day else 6)
    with connect() as conn:
        conn.execute('''INSERT INTO provider_budget_ledger VALUES
            ('Alpaca IEX','__provider__',%s,'configured',10,2,%s,NULL,NULL,%s,%s,'history',%s)''',
            (date.isoformat(), remaining, reason, (updated + timedelta(seconds=recovery.PROVIDER_PERMISSION_COOLDOWN_SECONDS)).isoformat(), updated.isoformat()))


def run(connect, fetch=None, now=NOW):
    return recovery.recover_alpaca_authentication(connect_factory=connect,
        fetch_history=fetch or (lambda *args, **kwargs: bars()), now_fn=lambda: now)


@pytest.mark.parametrize('same_day', [True, False])
def test_successful_replaced_pair_releases_only_original_authentication_record(db, same_day):
    seed(db, same_day=same_day)
    called = []
    def fetch(*args, **kwargs):
        called.append((args, kwargs))
        with db() as conn:
            assert conn.execute('SELECT COUNT(*) AS n FROM alpaca_auth_recovery_receipts').fetchone()['n'] == 1
            ledger = conn.execute('SELECT * FROM provider_budget_ledger WHERE utc_date=%s', (NOW.date().isoformat(),)).fetchall()
            assert {row['capability'] for row in ledger} == {'__provider__', 'us_history'}
            assert next(row for row in ledger if row['capability'] == 'us_history')['requests_used'] == 1
        return bars()
    result = run(db, fetch)
    assert result['status'] == 'AUTHENTICATED_IEX'
    assert result['http_status'] == 200 and result['rows'] == 1
    assert called == [(('MSFT', '5d', '1d'), {'max_pages': 1})]
    with db() as conn:
        row = conn.execute("SELECT * FROM provider_budget_ledger WHERE capability='__provider__' ORDER BY utc_date LIMIT 1").fetchone()
        assert row['last_failure'] == recovery.AUTH_REASON
        assert row['cooldown_until'] == NOW.isoformat()
        receipt = conn.execute('SELECT * FROM alpaca_auth_recovery_receipts').fetchone()
        assert receipt['status'] == 'AUTHENTICATED_IEX'
        assert receipt['receipt']['original_cooldowns'][0]['cooldown_until'] != NOW.isoformat()
    assert run(db, lambda *args, **kwargs: pytest.fail('restart must not re-probe'))['status'] == 'NO_AUTH_COOLDOWN'


@pytest.mark.parametrize('reason,recent', [('rate limited', False), ('unknown', False), (recovery.AUTH_REASON, True)])
def test_non_authentication_or_recent_update_cannot_be_bypassed(db, reason, recent):
    seed(db, reason=reason, recent=recent)
    assert run(db, lambda *a, **k: pytest.fail('cooldown must prevent HTTP'))['status'] == 'COOLDOWN_PRESERVED'


@pytest.mark.parametrize('failure', ['401', '403', '429', 'timeout', 'empty', 'identity', 'bad_book'])
def test_failures_never_release_old_cooldown_and_do_not_leak_credentials(db, caplog, failure):
    from alpaca_iex_provider import AlpacaDataError
    seed(db)
    def fetch(*args, **kwargs):
        if failure.isdigit():
            raise AlpacaDataError('unit-account-key unit-account-secret', status_code=int(failure))
        if failure == 'timeout':
            raise RuntimeError('unit-account-secret https://example.com/?token=secret')
        frame = bars()
        if failure == 'empty':
            return pd.DataFrame()
        if failure == 'identity':
            frame.attrs['provider_symbol'] = 'GOOGL'
        if failure == 'bad_book':
            frame.loc[:, 'High'] = 1.0
        return frame
    with caplog.at_level('INFO'):
        result = run(db, fetch)
    assert result['status'] != 'AUTHENTICATED_IEX'
    assert 'unit-account' not in caplog.text and 'https://' not in caplog.text
    with db() as conn:
        row = conn.execute("SELECT * FROM provider_budget_ledger WHERE capability='__provider__'").fetchone()
        assert recovery._dt(row['cooldown_until']) > NOW
        assert conn.execute('SELECT COUNT(*) AS n FROM alpaca_auth_recovery_receipts').fetchone()['n'] == 1
    assert run(db, lambda *a, **k: pytest.fail('same pair cannot re-probe across UTC dates'), now=NOW + timedelta(hours=20))['status'] == 'ALREADY_CHECKED'


def test_exhausted_quota_rolls_back_all_probe_reservations(db):
    seed(db, remaining=0)
    assert run(db, lambda *a, **k: pytest.fail('exhausted quota'))['status'] == 'BUDGET_BLOCKED'
    with db() as conn:
        assert conn.execute('SELECT requests_used FROM provider_budget_ledger').fetchone()['requests_used'] == 2
        assert conn.execute("SELECT COUNT(*) AS n FROM provider_budget_ledger WHERE capability='us_history'").fetchone()['n'] == 0


@pytest.mark.parametrize('code', [401, 429])
def test_concurrent_cooldown_update_during_successful_probe_is_preserved(db, code):
    seed(db)
    def fetch(*args, **kwargs):
        with db() as conn:
            mark_provider_cooldown_db(conn, 'Alpaca IEX', seconds=900,
                reason=recovery.AUTH_REASON if code == 401 else 'HTTP 429', now=NOW + timedelta(seconds=1))
        return bars()
    assert run(db, fetch)['status'] == 'CONCURRENT_CHANGE_PRESERVED'
    with db() as conn:
        row = conn.execute("SELECT * FROM provider_budget_ledger WHERE capability='__provider__'").fetchone()
        assert recovery._dt(row['cooldown_until']) > NOW
        assert row['updated_at'] == (NOW + timedelta(seconds=1)).isoformat()


def test_two_workers_and_restart_issue_only_one_request(db):
    seed(db)
    entered, release = Event(), Event()
    calls = []
    def fetch(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(10)
        return bars()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run, db, fetch)
        assert entered.wait(10)
        second = pool.submit(run, db, lambda *a, **k: pytest.fail('duplicate worker HTTP'))
        assert second.result(timeout=10)['status'] in {'COOLDOWN_PRESERVED', 'ALREADY_CHECKED'}
        release.set()
        assert first.result(timeout=10)['status'] == 'AUTHENTICATED_IEX'
    assert len(calls) == 1


def test_cross_midnight_hidden_rate_record_is_not_ignored(db):
    seed(db, same_day=False)
    with db() as conn:
        mark_provider_cooldown_db(conn, 'Alpaca IEX', seconds=900, reason='HTTP 429', now=NOW)
    assert run(db, lambda *a, **k: pytest.fail('new date429 must block'))['status'] == 'COOLDOWN_PRESERVED'


def test_history_quota_exhaustion_rolls_back_provider_charge(db):
    seed(db)
    with db() as conn:
        conn.execute('''INSERT INTO provider_budget_ledger VALUES
            ('Alpaca IEX','us_history',%s,'configured',10,10,0,NULL,NULL,NULL,NULL,'history',%s)''',
            (NOW.date().isoformat(), NOW.isoformat()))
    assert run(db, lambda *a, **k: pytest.fail('no history quota'))['status'] == 'BUDGET_BLOCKED'
    with db() as conn:
        assert conn.execute("SELECT requests_used FROM provider_budget_ledger WHERE capability='__provider__'").fetchone()['requests_used'] == 2


@pytest.mark.parametrize('provider,capability,reason,recent,matching', [
    ('Polygon', '__provider__', recovery.AUTH_REASON, False, True),
    ('Alpaca IEX', 'us_history', recovery.AUTH_REASON, False, True),
    ('Alpaca IEX', '__provider__', 'HTTP 429', False, True),
    ('Alpaca IEX', '__provider__', recovery.AUTH_REASON, True, True),
    ('Alpaca IEX', '__provider__', recovery.AUTH_REASON, False, False),
])
def test_budget_recheck_exception_cannot_bypass_other_cooldowns(db, provider, capability, reason, recent, matching):
    from global_adaptive_engine import reserve_provider_budget_db
    updated = NOW - timedelta(seconds=1) if recent else NOW - timedelta(hours=2)
    with db() as conn:
        conn.execute('''INSERT INTO provider_budget_ledger VALUES
            (%s,%s,%s,'configured',10,2,8,NULL,NULL,%s,%s,'history',%s)''',
            (provider, capability, NOW.date().isoformat(), reason, (NOW + timedelta(days=1)).isoformat(), updated.isoformat()))
        guard = dict(conn.execute('SELECT * FROM provider_budget_ledger').fetchone())
        if not matching:
            guard['updated_at'] = 'different'
        result = reserve_provider_budget_db(conn, provider, capability, daily_budget=10,
            now=NOW, auth_recheck_guard=guard)
        assert not result['reserved']
        assert conn.execute('SELECT requests_used FROM provider_budget_ledger').fetchone()['requests_used'] == 2


def test_failed_completion_connection_keeps_durable_claim_without_repeat_http(db):
    seed(db)
    count = []
    @contextmanager
    def disconnect_after_claim():
        count.append(1)
        if len(count) > 1:
            raise RuntimeError('private-connection-secret')
        with db() as conn:
            yield conn
    result = recovery.recover_alpaca_authentication(connect_factory=disconnect_after_claim,
        fetch_history=lambda *a, **k: bars(), now_fn=lambda: NOW)
    assert result['status'] == 'UNAVAILABLE'
    with db() as conn:
        assert conn.execute('SELECT status FROM alpaca_auth_recovery_receipts').fetchone()['status'] == 'CLAIMED'
        assert recovery._dt(conn.execute('SELECT cooldown_until FROM provider_budget_ledger').fetchone()['cooldown_until']) > NOW
    assert run(db, lambda *a, **k: pytest.fail('crash must not duplicate HTTP'),
               now=NOW + timedelta(hours=1))['status'] == 'ALREADY_CHECKED'


def test_older_masked_rate_update_remains_ambiguous_even_after_rate_wait(db):
    seed(db)
    with db() as conn:
        conn.execute("UPDATE provider_budget_ledger SET updated_at=%s", ((NOW - timedelta(hours=1)).isoformat(),))
    assert run(db, lambda *a, **k: pytest.fail('ambiguous rate history cannot be cleared'))['status'] == 'COOLDOWN_PRESERVED'


def test_replaced_credentials_can_recover_with_legacy_trading_endpoint_and_feed(db, monkeypatch, caplog):
    import alpaca_iex_provider as alpaca
    from types import SimpleNamespace
    seed(db)
    monkeypatch.setenv('ALPACA_DATA_URL', 'https://paper-api.alpaca.markets/?token=private-value')
    monkeypatch.setenv('ALPACA_DATA_FEED', 'sip')
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: {'symbol': 'MSFT', 'bars': [
            {'t': (NOW-timedelta(days=1)).isoformat(), 'o': 100, 'h': 102, 'l': 99, 'c': 101, 'v': 1000}]})
    monkeypatch.setattr(alpaca.requests, 'get', get)
    with caplog.at_level('INFO'):
        result = run(db, alpaca.iex_stock_history)
    assert result['status'] == 'AUTHENTICATED_IEX'
    assert len(calls) == 1
    assert calls[0][0] == 'https://data.alpaca.markets/v2/stocks/MSFT/bars'
    assert calls[0][1]['params']['feed'] == 'iex'
    assert calls[0][1]['allow_redirects'] is False
    assert 'legacy_endpoint_ignored=True' in caplog.text and 'legacy_feed_ignored=True' in caplog.text
    assert 'private-value' not in caplog.text
