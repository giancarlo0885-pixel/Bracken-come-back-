"""Actual ledger recovery in UUID schemas on a guarded disposable local DB."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from threading import Barrier
import uuid

import pytest
import global_adaptive_engine as ledger
from provider_budget_diagnostics import read_public_research_budgets

DAY = datetime(2026, 10, 20, tzinfo=timezone.utc)
CAPABILITY = '__provider__'


@pytest.fixture
def budget_db():
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict
    from psycopg.rows import dict_row
    dsn = os.getenv('DATABASE_URL', '')
    if not dsn:pytest.skip('PostgreSQL integration only')
    conf = conninfo_to_dict(dsn)
    if conf.get('host') not in {'localhost','127.0.0.1','::1'} or not conf.get('dbname','').endswith('_test'):
        pytest.skip('Explicit disposable local test database required')
    schema = 'budget_marker_' + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    def connect():
        return psycopg.connect(dsn, options='-c search_path='+schema+',pg_catalog -c statement_timeout=5000', row_factory=dict_row)
    try:
        with connect() as conn:
            conn.execute('''CREATE TABLE provider_budget_ledger (
                provider TEXT,capability TEXT,utc_date TEXT,entitlement TEXT,daily_budget INT,
                requests_used INT,remaining_budget INT,latency_ms FLOAT8,last_success TEXT,
                last_failure TEXT,cooldown_until TEXT,data_mode TEXT,updated_at TEXT,
                PRIMARY KEY(provider,capability,utc_date))''')
        yield connect
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def reserve(conn, provider='Coinbase Public Research', *, budget=2, now=DAY+timedelta(minutes=6)):
    return ledger.reserve_provider_budget_db(conn, provider, CAPABILITY, daily_budget=budget,
        entitlement='public', data_mode='shadow_research', now=now)


def record(conn, provider='Coinbase Public Research'):
    return conn.execute('SELECT * FROM provider_budget_ledger WHERE provider=%s AND utc_date=%s',
                        (provider,DAY.date().isoformat())).fetchone()


@pytest.mark.parametrize('provider', ['Coinbase Public Research','Kraken Public Research'])
def test_post_midnight_429_marker_recovers_once_without_replenishing_spent_allowance(budget_db, provider):
    with budget_db() as conn:
        assert reserve(conn,provider,now=DAY-timedelta(seconds=1))['reserved']
        ledger.mark_provider_cooldown_db(conn,provider,seconds=300,reason='HTTP 429',now=DAY+timedelta(seconds=1))
    with budget_db() as conn:
        original = record(conn,provider)
        assert original['daily_budget'] == original['requests_used'] == original['remaining_budget'] == 0
        assert read_public_research_budgets(conn,now=DAY+timedelta(seconds=2))['providers'][provider]['exhaustion_proven_by_ledger'] is False
        assert not reserve(conn,provider,now=DAY+timedelta(minutes=2))['reserved']
        assert record(conn,provider) == original
    with budget_db() as conn:
        result = reserve(conn,provider)
        assert result['reserved'] and result['remaining'] == 1
        row = record(conn,provider)
        assert (row['daily_budget'],row['requests_used'],row['remaining_budget']) == (2,1,1)
        assert row['last_failure'] == original['last_failure'] and row['cooldown_until'] == original['cooldown_until']
        assert row['data_mode'] == 'shadow_research'
        # A later caller cannot enlarge/reinitialize the already installed budget.
        assert reserve(conn,provider,budget=50)['remaining'] == 0
        denied = reserve(conn,provider,budget=50)
        assert not denied['reserved'] and 'exhausted' in denied['reason']
        row = record(conn,provider)
        assert (row['daily_budget'],row['requests_used'],row['remaining_budget']) == (2,2,0)
        assert read_public_research_budgets(conn,now=DAY+timedelta(minutes=6))['providers'][provider]['exhaustion_proven_by_ledger'] is True
        yesterday = conn.execute('SELECT requests_used FROM provider_budget_ledger WHERE provider=%s AND utc_date=%s',
            (provider,(DAY-timedelta(days=1)).date().isoformat())).fetchone()
        assert yesterday['requests_used'] == 1


def test_concurrent_first_reservations_initialize_one_allowance(budget_db):
    with budget_db() as conn:
        ledger.mark_provider_cooldown_db(conn,'Coinbase Public Research',seconds=1,reason='HTTP 429',now=DAY)
    start = Barrier(2)
    def attempt():
        with budget_db() as conn:
            start.wait(timeout=5)
            return reserve(conn,budget=1)['reserved']
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt) for _ in range(2)]
        assert sorted(f.result(timeout=10) for f in futures) == [False,True]
    with budget_db() as conn:
        row = record(conn)
        assert (row['daily_budget'],row['requests_used'],row['remaining_budget']) == (1,1,0)


@pytest.mark.parametrize('mode,budget,used', [
    ('shared_cooldown',2,2), # An exhausted configured budget must never reset.
    ('shadow_research',0,0), # Explicit zero capacity is not a cooldown marker.
    ('shared_cooldown',0,1), # Malformed/used markers are not pristine placeholders.
])
def test_initialized_zero_or_nonpristine_rows_are_never_refilled(budget_db, mode, budget, used):
    with budget_db() as conn:
        conn.execute('''INSERT INTO provider_budget_ledger
            (provider,capability,utc_date,daily_budget,requests_used,remaining_budget,data_mode)
            VALUES('Coinbase Public Research',%s,%s,%s,%s,0,%s)''',
            (CAPABILITY,DAY.date().isoformat(),budget,used,mode))
        before = record(conn)
        result = reserve(conn,budget=50)
        assert not result['reserved']
        assert record(conn) == before
        if budget == 0:assert 'exhausted' not in result['reason']


def test_zero_configured_allowance_cannot_initialize_a_marker(budget_db):
    with budget_db() as conn:
        ledger.mark_provider_cooldown_db(conn,'Coinbase Public Research',seconds=1,now=DAY)
        before = record(conn)
        denied = reserve(conn,budget=0)
        assert not denied['reserved'] and 'exhausted' not in denied['reason']
        assert record(conn) == before


@pytest.mark.parametrize('invalid_expiry', [None, '', 'not-a-timestamp', '2026-10-20T00:00:00', '2026-10-20'])
def test_missing_or_malformed_cooldown_expiry_never_initializes_capacity(budget_db, invalid_expiry):
    with budget_db() as conn:
        ledger.mark_provider_cooldown_db(conn,'Coinbase Public Research',seconds=1,now=DAY)
        conn.execute('UPDATE provider_budget_ledger SET cooldown_until=%s', (invalid_expiry,))
        before = record(conn)
        result = reserve(conn)
        assert not result['reserved'] and 'exhausted' not in result['reason']
        assert record(conn) == before


def test_initialization_and_reservation_roll_back_together(budget_db):
    with budget_db() as conn:
        ledger.mark_provider_cooldown_db(conn,'Coinbase Public Research',seconds=1,now=DAY)
        before = record(conn)
    with budget_db() as conn:
        assert reserve(conn)['reserved']
        conn.rollback()
    with budget_db() as conn:
        assert record(conn) == before
        assert reserve(conn)['remaining'] == 1


@pytest.mark.parametrize('provider', ['Coinbase Public Research','Kraken Public Research'])
def test_spent_day_remains_closed_and_next_utc_day_receives_only_normal_allowance(budget_db,provider):
    """Recovery must use the next UTC allocation, never refill an exhausted day."""
    last_minute=DAY+timedelta(hours=23,minutes=59)
    with budget_db() as conn:
        assert reserve(conn,provider,budget=1,now=last_minute)['reserved']
        before=record(conn,provider)
        assert (before['daily_budget'],before['requests_used'],before['remaining_budget'])==(1,1,0)
        assert not reserve(conn,provider,budget=2400,now=last_minute+timedelta(seconds=30))['reserved']
        assert record(conn,provider)==before
    with budget_db() as conn:
        tomorrow=DAY+timedelta(days=1)
        result=reserve(conn,provider,budget=1,now=tomorrow)
        assert result['reserved'] and result['remaining']==0
        assert not reserve(conn,provider,budget=2400,now=tomorrow+timedelta(seconds=1))['reserved']
        assert record(conn,provider)==before
        today=conn.execute('SELECT daily_budget,requests_used,remaining_budget FROM provider_budget_ledger WHERE provider=%s AND utc_date=%s',
                          (provider,tomorrow.date().isoformat())).fetchone()
        assert (today['daily_budget'],today['requests_used'],today['remaining_budget'])==(1,1,0)
