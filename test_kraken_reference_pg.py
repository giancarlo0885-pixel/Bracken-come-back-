"""Actual PostgreSQL election and bounded state; local disposable schemas only."""
from contextlib import contextmanager
from datetime import timedelta
import os
import time
import uuid

import pytest

from kraken_reference_runtime import STATE_DDL, claim_owner, write_state, display_state
from test_kraken_reference_book import NOW, ready


@pytest.fixture
def pg():
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict
    from psycopg.rows import dict_row
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn: pytest.skip("PostgreSQL integration only")
    settings = conninfo_to_dict(dsn)
    if (settings.get("host") not in {"localhost", "127.0.0.1", "::1"}
            or not (settings.get("dbname", "").endswith("_test") or settings.get("dbname", "").startswith("test_"))):
        pytest.skip("Disposable local test database required")
    schema = "kraken_test_" + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    @contextmanager
    def connection():
        with psycopg.connect(dsn, options="-c search_path="+schema+",pg_catalog", row_factory=dict_row, autocommit=True) as conn:
            yield conn
    try:
        with connection() as conn: conn.execute(STATE_DDL)
        yield connection
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_one_session_owns_feed_and_disconnect_releases_lock(pg):
    with pg() as first, pg() as second:
        assert claim_owner(first)
        assert not claim_owner(second)
        first.close()  # Model owner process/DB connection failure, no explicit unlock.
        # Server observes socket closure asynchronously; use a bounded handoff wait.
        deadline = time.monotonic() + 2
        while not claim_owner(second):
            assert time.monotonic() < deadline
            time.sleep(0.01)


def test_latest_state_is_singleton_and_reader_cannot_reanimate_stale_data(pg):
    payload = ready().health(NOW)
    with pg() as conn:
        for i in range(20): write_state(conn, payload, NOW+timedelta(seconds=i))
        assert conn.execute("SELECT count(*) AS n FROM kraken_reference_state").fetchone()["n"] == 1
    with pg() as reader:
        reader.execute("BEGIN READ ONLY")
        row = reader.execute("SELECT * FROM kraken_reference_state").fetchone()
        assert not display_state(row["payload"], row["updated_at"], NOW+timedelta(seconds=20))["quotes"]
        with pytest.raises(Exception): write_state(reader, payload, NOW)
        reader.execute("ROLLBACK")


def test_singleton_constraint_prevents_tick_history_growth(pg):
    import psycopg
    with pg() as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO kraken_reference_state VALUES (2,%s,'{}')", (NOW,))


def test_reader_expires_persisted_primary_comparison_without_changing_evidence(pg):
    from kraken_reference_book import compare_reference
    from test_kraken_reference_book import primary
    payload = ready().health(NOW)
    comparison = compare_reference(payload['quotes']['BTC-USD'], primary(
        quote_timestamp=(NOW-timedelta(seconds=2)).isoformat()), NOW)
    payload['comparisons'] = {'BTC-USD': {**comparison, 'checked_at': NOW.isoformat()}}
    with pg() as conn:
        write_state(conn, payload, NOW)
    with pg() as reader:
        reader.execute('BEGIN READ ONLY')
        row = reader.execute('SELECT * FROM kraken_reference_state').fetchone()
        displayed = display_state(row['payload'], row['updated_at'], NOW+timedelta(seconds=9))
        assert 'BTC-USD' in displayed['quotes']
        assert not displayed['comparisons']
        assert row['payload']['comparisons'] == payload['comparisons']
        reader.execute('ROLLBACK')


def test_persisted_pair_keeps_its_original_book_and_expiry(pg, monkeypatch):
    from unittest.mock import Mock
    from kraken_reference_runtime import KrakenReferenceService
    from test_kraken_venue_comparison import quote
    from test_kraken_reference_book import message
    import kraken_reference_runtime as runtime
    service=KrakenReferenceService(connect_factory=Mock());service.state=ready()
    service.remember_references(NOW)
    service.observe_response(['BTC-USD'],[quote()],NOW)
    later=NOW+timedelta(seconds=5)
    service.state.ingest(message(when=later),later);service.remember_references(later)
    monkeypatch.setattr(runtime,'utc_now',lambda:later)
    with pg() as owner: service.publish(owner)
    with pg() as reader:
        reader.execute('BEGIN READ ONLY')
        row=reader.execute('SELECT * FROM kraken_reference_state').fetchone()
        shown=display_state(row['payload'],row['updated_at'],later)
        assert shown['comparisons']['BTC-USD']['reference_timestamp']==NOW.isoformat()
        assert shown['quotes']['BTC-USD']['quote_timestamp']==later.isoformat()
        assert shown['comparisons']['BTC-USD']['reference_quote']['execution_quote_eligible'] is False
        assert not display_state(row['payload'],NOW+timedelta(seconds=11),NOW+timedelta(seconds=11))['comparisons']
        reader.execute('ROLLBACK')


def test_research_evidence_is_exact_bounded_immutable_and_separate(pg):
    import json,zlib
    from kraken_primary_research import EVIDENCE_DDL, DDL, persist_evidence, read_research
    from kraken_research_data import ResearchUnavailable
    from test_kraken_primary_research import history,quote,prediction
    from kraken_primary_research import research_forecast
    from database import CANONICAL_PROTECTED_TABLES
    assert 'kraken_research_evidence' in CANONICAL_PROTECTED_TABLES
    h=history(); result=research_forecast(quote(),h,NOW,forecast=prediction)
    with pg() as conn:
        conn.execute(EVIDENCE_DDL);conn.execute(DDL)
        assert persist_evidence(conn,result,h)==1
        with pytest.raises(ResearchUnavailable,match='BAR_ALREADY_RECORDED'):
            persist_evidence(conn,result,h)
        row=conn.execute('SELECT * FROM kraken_research_evidence').fetchone()
        assert row['payload']==result and zlib.decompress(row['closed_history']).decode()==h.to_csv()
        # Simulate the last fixed storage slot without creating 5000 fake outcomes.
        conn.execute('INSERT INTO kraken_research_evidence VALUES(5000,%s,%s,%s::jsonb,%s)',
            ('capacity-test',NOW,json.dumps({'fixture':True}),b''))
        other={**result,'history_last_closed_bar':'different-fixture-bar'}
        with pytest.raises(ResearchUnavailable,match='CAPACITY'):
            persist_evidence(conn,other,h)
        assert conn.execute('SELECT count(*) AS n FROM kraken_research_evidence').fetchone()['n']==2
        conn.execute('INSERT INTO kraken_research_state VALUES(1,%s,%s::jsonb)',(NOW,json.dumps({'symbols':{'BTC-USD':result}})))
        view=read_research(lambda q:conn.execute(q).fetchall(),NOW+timedelta(seconds=11))
        assert view['symbols']['BTC-USD']['current'] is False
        assert view['execution_authority']=='NONE'


def test_disabled_research_reader_works_before_optional_table_exists(pg):
    from kraken_primary_research import read_research
    with pg() as conn:
        conn.execute('BEGIN READ ONLY')
        view=read_research(lambda q:conn.execute(q).fetchall(),NOW)
        assert view['status']=='UNAVAILABLE' and view['symbols']=={}
        # A missing-relation query would abort this read-only transaction.
        assert conn.execute('SELECT 1 AS ok').fetchone()['ok']==1
        conn.execute('ROLLBACK')
