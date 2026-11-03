"""Additive storage cannot rename, delete or certify historical research as trades."""
from pathlib import Path
from unittest.mock import Mock
import json
import time

import pytest
from test_kraken_reference_pg import pg
from test_kraken_reference_book import NOW
from test_kraken_primary_research import history,quote,prediction
from kraken_primary_research import EVIDENCE_DDL,persist_evidence,research_forecast
from kraken_brain_evidence import research_source,sync_research_sources,committed_receipt,read_evidence_capacity
from kraken_research_store import ensure_overflow_schema,evidence_layout,evidence_sql,read_storage_footprint,OVERFLOW_MIGRATION_LOCK


def activate(monkeypatch):
    for k,v in {'KRAKEN_RESEARCH_OVERFLOW_CREATE_ENABLED':'true','EXECUTION_MODE':'paper',
                'ENABLE_BROKER_SUBMISSION':'false','LIVE_TRADING_ARMED':'false'}.items():
        monkeypatch.setenv(k,v)


@pytest.mark.parametrize('key,value', [('KRAKEN_RESEARCH_OVERFLOW_CREATE_ENABLED',None),
    ('KRAKEN_RESEARCH_OVERFLOW_CREATE_ENABLED','false'),('EXECUTION_MODE','live'),
    ('ENABLE_BROKER_SUBMISSION','true'),('LIVE_TRADING_ARMED','true')])
def test_creation_requires_explicit_paper_only_gate(monkeypatch,key,value):
    activate(monkeypatch)
    if value is None:monkeypatch.delenv(key)
    else:monkeypatch.setenv(key,value)
    conn=Mock()
    assert ensure_overflow_schema(conn) is False
    conn.execute.assert_not_called()
    conn.transaction.assert_not_called()


@pytest.mark.parametrize('missing_key', [
    'EXECUTION_MODE', 'ENABLE_BROKER_SUBMISSION', 'LIVE_TRADING_ARMED',
])
def test_overflow_creation_fails_closed_if_safety_setting_missing(monkeypatch, missing_key):
    activate(monkeypatch)
    monkeypatch.delenv(missing_key)
    conn = Mock()
    assert ensure_overflow_schema(conn) is False
    conn.transaction.assert_not_called()
    conn.execute.assert_not_called()


def test_overflow_creation_fails_closed_if_all_safety_settings_missing(monkeypatch):
    activate(monkeypatch)
    for key in ('EXECUTION_MODE', 'ENABLE_BROKER_SUBMISSION', 'LIVE_TRADING_ARMED'):
        monkeypatch.delenv(key)
    conn = Mock()
    assert ensure_overflow_schema(conn) is False
    conn.transaction.assert_not_called()
    conn.execute.assert_not_called()


@pytest.mark.parametrize('table,identity', [('kraken_research_evidence',5001),
    ('kraken_research_evidence_overflow',5000),('kraken_research_evidence_overflow',10001),
    ('untrusted_table',1),('kraken_research_evidence',True)])
def test_storage_identity_mismatch_cannot_enter_brain(table,identity):
    result=research_forecast(quote(),history(),NOW,forecast=prediction)
    with pytest.raises(ValueError,match='storage identity'):
        research_source({'id':identity,'source_table':table,'observed_at':NOW,'payload':result})


def test_projection_and_layout_are_static_not_external_sql():
    with pytest.raises(ValueError):evidence_sql({'tables':('other',)},'id')
    with pytest.raises(ValueError):evidence_sql({'tables':('kraken_research_evidence',)},'id;DROP')
    conn=Mock();conn.execute.return_value.fetchone.return_value={}
    with pytest.raises(ValueError):evidence_layout(conn)


def test_storage_read_failure_is_explicit_and_scrubbed():
    conn=Mock();conn.execute.side_effect=RuntimeError('private credential text')
    assert read_storage_footprint(conn)=={'status':'UNAVAILABLE','reason_class':'RuntimeError','execution_impact':'NONE'}


def prepare_tables(conn):
    conn.execute(EVIDENCE_DDL)
    conn.execute('CREATE TABLE oracle_brain_entries(id BIGSERIAL PRIMARY KEY)')
    conn.execute((Path(__file__).parent/'migrations/20260920_oracle_brain_learning_v2.sql').read_text())


def test_pg_original_rows_and_brain_keys_survive_extension(pg,monkeypatch):
    activate(monkeypatch)
    h=history();result=research_forecast(quote(),h,NOW,forecast=prediction)
    with pg() as conn:
        prepare_tables(conn)
        assert persist_evidence(conn,result,h)==1
        conn.execute('INSERT INTO kraken_research_evidence VALUES(5000,%s,%s,%s::jsonb,%s)',
                     ('last-legacy-fixture',NOW,json.dumps(result),b'unchanged-legacy'))
        sync_research_sources(conn)
        oldrows=conn.execute('SELECT * FROM kraken_research_evidence ORDER BY id').fetchall()
        oldbrain=conn.execute('SELECT * FROM oracle_brain_sources ORDER BY source_key').fetchall()
        assert ensure_overflow_schema(conn)
        assert ensure_overflow_schema(conn)  # Repeatable without a rewrite.
        assert read_evidence_capacity(conn)['remaining_id_slots']==5000
        from dataclasses import replace
        def revised_model(*args,**kwargs):
            p=prediction(*args,**kwargs)
            return replace(p,model_version=p.model_version+'-fixture-v2',forecast_id=p.forecast_id+'-fixture-v2')
        other=research_forecast(quote(),h,NOW,forecast=revised_model)
        assert persist_evidence(conn,other,h)==5001
        assert sync_research_sources(conn)['inserted']==1
        receipt=committed_receipt(conn)
        assert (receipt['expected'],receipt['matched'],receipt['unmatched'])==(3,3,0)
        assert receipt['evidence_capacity']['max_id']==10000
        assert receipt['storage_footprint']['status']=='MEASURED'
        assert conn.execute('SELECT * FROM kraken_research_evidence ORDER BY id').fetchall()==oldrows
        assert conn.execute("SELECT * FROM oracle_brain_sources WHERE source_key IN ('kraken-research:1','kraken-research:5000') ORDER BY source_key").fetchall()==oldbrain
        source=conn.execute("SELECT source_ref,metadata FROM oracle_brain_sources WHERE source_key='kraken-research:5001'").fetchone()
        assert source['source_ref']=='kraken_research_evidence_overflow:5001'
        assert source['metadata']['trade_outcome'] is False
        assert source['metadata']['evidence']==other
        # Legacy writer behavior remains capped, never overwrites the old rows.
        old_insert=conn.execute("INSERT INTO kraken_research_evidence SELECT COALESCE(MAX(id),0)+1,%s,NOW(),'{}',%s FROM kraken_research_evidence HAVING COALESCE(MAX(id),0)<5000 RETURNING id",('legacy-writer-fixture',b''))
        assert old_insert.fetchone() is None
        assert conn.execute('SELECT * FROM kraken_research_evidence ORDER BY id').fetchall()==oldrows
        monkeypatch.setenv('KRAKEN_RESEARCH_OVERFLOW_CREATE_ENABLED','false')
        assert ensure_overflow_schema(conn) is False
        assert committed_receipt(conn)['expected']==3  # Disabling creation never hides old/new evidence.


def test_pg_deduplication_full_boundary_and_retention_protection(pg,monkeypatch):
    from database import CANONICAL_PROTECTED_TABLES,DATABASE_RETENTION_POLICIES
    from kraken_research_data import ResearchUnavailable
    assert 'kraken_research_evidence_overflow' in CANONICAL_PROTECTED_TABLES
    assert 'kraken_research_evidence_overflow' not in DATABASE_RETENTION_POLICIES
    activate(monkeypatch)
    h=history();result=research_forecast(quote(),h,NOW,forecast=prediction)
    with pg() as conn:
        prepare_tables(conn)
        assert persist_evidence(conn,result,h)==1
        assert ensure_overflow_schema(conn)
        with pytest.raises(ResearchUnavailable,match='BAR_ALREADY_RECORDED'):persist_evidence(conn,result,h)
        conn.execute("INSERT INTO kraken_research_evidence_overflow VALUES(10000,%s,%s,'{}',%s)",('last-overflow-fixture',NOW,b''))
        before=conn.execute('SELECT * FROM kraken_research_evidence_overflow').fetchall()
        assert read_evidence_capacity(conn)['status']=='CAPACITY_REACHED'
        other={**result,'symbol':'ETH-USD'}
        with pytest.raises(ResearchUnavailable,match='CAPACITY'):persist_evidence(conn,other,h)
        assert conn.execute('SELECT * FROM kraken_research_evidence_overflow').fetchall()==before


def test_pg_migration_lock_timeout_rolls_back_without_touching_originals(pg,monkeypatch):
    import psycopg
    activate(monkeypatch)
    with pg() as blocker,pg() as candidate:
        blocker.execute(EVIDENCE_DDL)
        blocker.execute('BEGIN')
        blocker.execute('SELECT pg_advisory_xact_lock(%s)',(OVERFLOW_MIGRATION_LOCK,))
        start=time.monotonic()
        with pytest.raises(psycopg.errors.LockNotAvailable):ensure_overflow_schema(candidate)
        assert time.monotonic()-start<3
        assert candidate.execute("SELECT to_regclass('kraken_research_evidence_overflow') AS relation").fetchone()['relation'] is None
        assert candidate.execute("SELECT to_regclass('kraken_research_evidence') AS relation").fetchone()['relation'] is not None
        blocker.execute('ROLLBACK')
        assert ensure_overflow_schema(candidate)


@pytest.mark.parametrize('identity,payload,history_bytes',[
    (5000,{},b''),(10001,{},b''),(5001,{'large':'x'*33000},b''),
    (5001,{},b'x'*131073),
])
def test_pg_overflow_enforces_ids_and_payload_bounds(pg,monkeypatch,identity,payload,history_bytes):
    import psycopg
    activate(monkeypatch)
    with pg() as conn:
        conn.execute(EVIDENCE_DDL)
        ensure_overflow_schema(conn)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute('INSERT INTO kraken_research_evidence_overflow VALUES(%s,%s,%s,%s::jsonb,%s)',
                         (identity,'invalid-fixture',NOW,json.dumps(payload),history_bytes))
        assert conn.execute('SELECT count(*) AS n FROM kraken_research_evidence_overflow').fetchone()['n']==0


def test_pg_outer_transaction_rollback_removes_only_uncommitted_new_schema(pg,monkeypatch):
    activate(monkeypatch)
    with pg() as conn:
        conn.execute(EVIDENCE_DDL)
        with pytest.raises(RuntimeError,match='rollback fixture'):
            with conn.transaction():
                assert ensure_overflow_schema(conn)
                raise RuntimeError('rollback fixture')
        assert conn.execute("SELECT to_regclass('kraken_research_evidence_overflow') AS relation").fetchone()['relation'] is None
        assert conn.execute("SELECT to_regclass('kraken_research_evidence') AS relation").fetchone()['relation'] is not None


def test_pg_writer_election_serializes_old_new_handoff(pg,monkeypatch):
    from kraken_primary_research import RESEARCH_LOCK
    activate(monkeypatch)
    with pg() as old,pg() as new:
        old.execute(EVIDENCE_DDL)
        assert old.execute('SELECT pg_try_advisory_lock(%s) AS owned',(RESEARCH_LOCK,)).fetchone()['owned']
        assert not new.execute('SELECT pg_try_advisory_lock(%s) AS owned',(RESEARCH_LOCK,)).fetchone()['owned']
        assert old.execute('SELECT pg_advisory_unlock(%s) AS released',(RESEARCH_LOCK,)).fetchone()['released']
        assert new.execute('SELECT pg_try_advisory_lock(%s) AS owned',(RESEARCH_LOCK,)).fetchone()['owned']
        assert ensure_overflow_schema(new)
        # The legacy object remains capped and readable to a mixed-version reader.
        assert old.execute('SELECT COALESCE(MAX(id),0) AS n FROM kraken_research_evidence').fetchone()['n']==0
        assert new.execute('SELECT pg_advisory_unlock(%s) AS released',(RESEARCH_LOCK,)).fetchone()['released']


@pytest.mark.parametrize('field,value', [('source_table','kraken_research_evidence'), ('evidence_id',5002), ('source_ref','kraken_research_evidence:5001')])
def test_pg_wrong_brain_table_identity_does_not_match_receipt(pg,monkeypatch,field,value):
    activate(monkeypatch)
    result=research_forecast(quote(),history(),NOW,forecast=prediction)
    with pg() as conn:
        prepare_tables(conn);ensure_overflow_schema(conn)
        conn.execute('INSERT INTO kraken_research_evidence_overflow VALUES(5001,%s,%s,%s::jsonb,%s)',
                     ('receipt-fixture',NOW,json.dumps(result),b'original-fixture'))
        assert sync_research_sources(conn)['inserted']==1
        assert committed_receipt(conn)['matched']==1
        # Corrupt only disposable fixture metadata to exercise the audit boundary.
        if field == 'source_ref':
            conn.execute("UPDATE oracle_brain_sources SET source_ref=%s WHERE source_key='kraken-research:5001'",(value,))
        else:
            conn.execute("UPDATE oracle_brain_sources SET metadata=jsonb_set(metadata,%s,%s::jsonb) WHERE source_key='kraken-research:5001'",([field],json.dumps(value)))
        receipt=committed_receipt(conn)
        assert receipt['matched']==0 and receipt['unmatched']==1 and receipt['sample']==[]


def test_pg_full_cycle_resumes_new_durable_research_and_brain_copy(pg,monkeypatch):
    import forecasting
    import kraken_primary_research as research
    import kraken_reference_runtime as runtime
    activate(monkeypatch)
    class Clock:
        @staticmethod
        def now(tz):return NOW
    monkeypatch.setattr(research,'datetime',Clock)
    monkeypatch.setattr(forecasting,'forecast_price',prediction)
    monkeypatch.setattr(runtime,'read_kraken_reference_state',lambda:{'subscribed_symbols':['BTC-USD'],'quotes':{'BTC-USD':quote()}})
    monkeypatch.setattr(research,'coinbase_quote',lambda *args:{**quote(),'provider':'Coinbase Exchange public reference','checksum_status':'NOT_PROVIDED'})
    client=Mock();client.history.return_value=history()
    with pg() as conn:
        prepare_tables(conn)
        seed=research_forecast(quote(),history(),NOW,forecast=prediction)
        conn.execute('INSERT INTO kraken_research_evidence VALUES(5000,%s,%s,%s::jsonb,%s)',
                     ('legacy-capacity-fixture',NOW,json.dumps(seed),b'original-history-fixture'))
        before=conn.execute('SELECT * FROM kraken_research_evidence').fetchall()
        ensure_overflow_schema(conn)
        conn.execute(research.DDL)
        research.PrimaryResearch(data=client).cycle(conn)
        state=conn.execute('SELECT payload FROM kraken_research_state WHERE id=1').fetchone()['payload']
        assert state['status']=='ACTIVE' and state['symbols']['BTC-USD']['evidence_id']==5001
        assert state['symbols']['BTC-USD']['forecast']['validation_status']=='shadow'
        assert state['execution_authority']=='NONE' and state['broker_submission']=='NONE'
        receipt=committed_receipt(conn)
        assert (receipt['expected'],receipt['matched'],receipt['unmatched'])==(2,2,0)
        assert conn.execute('SELECT * FROM kraken_research_evidence').fetchall()==before
        assert client.history.call_count==1

def test_pg_capacity_headroom_uses_per_table_indexable_max_without_rewriting_evidence(pg,monkeypatch):
    """Preserve sparse immutable IDs across both primary-key indexed tables."""
    activate(monkeypatch)
    with pg() as conn:
        conn.execute(EVIDENCE_DDL)
        assert read_evidence_capacity(conn)['highest_id'] == 0
        conn.execute("INSERT INTO kraken_research_evidence VALUES(1,%s,%s,'{}',%s)",
                     ('first-original',NOW,b'original-one'))
        conn.execute("INSERT INTO kraken_research_evidence VALUES(4999,%s,%s,'{}',%s)",
                     ('last-original',NOW,b'original-last'))
        before = conn.execute('SELECT * FROM kraken_research_evidence ORDER BY id').fetchall()
        assert read_evidence_capacity(conn)['remaining_id_slots'] == 1
        assert ensure_overflow_schema(conn)
        assert read_evidence_capacity(conn)['remaining_id_slots'] == 5001
        conn.execute("INSERT INTO kraken_research_evidence_overflow VALUES(5001,%s,%s,'{}',%s)",
                     ('first-overflow',NOW,b'overflow-one'))
        conn.execute("INSERT INTO kraken_research_evidence_overflow VALUES(9999,%s,%s,'{}',%s)",
                     ('last-overflow',NOW,b'overflow-last'))
        overflow_before = conn.execute('SELECT * FROM kraken_research_evidence_overflow ORDER BY id').fetchall()
        capacity = read_evidence_capacity(conn)
        assert capacity['highest_id'] == 9999
        assert capacity['remaining_id_slots'] == 1
        assert capacity['status'] == 'CAPACITY_CRITICAL'
        assert conn.execute('SELECT * FROM kraken_research_evidence ORDER BY id').fetchall() == before
        assert conn.execute('SELECT * FROM kraken_research_evidence_overflow ORDER BY id').fetchall() == overflow_before

