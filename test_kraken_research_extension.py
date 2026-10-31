"""Forward-only third evidence segment: no rewrites, broker authority or backfill."""
import json
from unittest.mock import Mock

import pytest
from test_kraken_reference_pg import pg
from test_kraken_reference_book import NOW
from test_kraken_primary_research import history, quote, prediction
from kraken_primary_research import EVIDENCE_DDL, persist_evidence, research_forecast
from kraken_research_store import (ensure_overflow_schema, ensure_extension_schema,
    evidence_layout, evidence_sql, read_storage_footprint, EXTENSION_TABLE)
from kraken_brain_evidence import sync_research_sources, committed_receipt, read_evidence_capacity
from kraken_research_data import ResearchUnavailable


def activate(monkeypatch):
    for key, value in {'KRAKEN_RESEARCH_OVERFLOW_CREATE_ENABLED': 'true',
            'KRAKEN_RESEARCH_EXTENSION_CREATE_ENABLED': 'true', 'EXECUTION_MODE': 'paper',
            'ENABLE_BROKER_SUBMISSION': 'false', 'LIVE_TRADING_ARMED': 'false'}.items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize('key,value', [
    ('KRAKEN_RESEARCH_EXTENSION_CREATE_ENABLED', 'false'),
    ('EXECUTION_MODE', 'live'), ('ENABLE_BROKER_SUBMISSION', 'true'),
    ('LIVE_TRADING_ARMED', 'true')])
def test_extension_creation_requires_explicit_paper_safety(monkeypatch, key, value):
    activate(monkeypatch)
    monkeypatch.setenv(key, value)
    conn = Mock()
    assert ensure_extension_schema(conn) is False
    conn.execute.assert_not_called()


@pytest.mark.parametrize('missing_key', [
    'EXECUTION_MODE', 'ENABLE_BROKER_SUBMISSION', 'LIVE_TRADING_ARMED',
])
def test_extension_creation_fails_closed_if_safety_setting_missing(monkeypatch, missing_key):
    activate(monkeypatch)
    monkeypatch.delenv(missing_key)
    conn = Mock()
    assert ensure_extension_schema(conn) is False
    conn.transaction.assert_not_called()
    conn.execute.assert_not_called()


def test_extension_creation_fails_closed_if_all_safety_settings_missing(monkeypatch):
    activate(monkeypatch)
    for key in ('EXECUTION_MODE', 'ENABLE_BROKER_SUBMISSION', 'LIVE_TRADING_ARMED'):
        monkeypatch.delenv(key)
    conn = Mock()
    assert ensure_extension_schema(conn) is False
    conn.transaction.assert_not_called()
    conn.execute.assert_not_called()


def test_extension_cannot_skip_existing_overflow(pg, monkeypatch):
    activate(monkeypatch)
    with pg() as conn:
        conn.execute(EVIDENCE_DDL)
        with pytest.raises(ValueError, match='Original overflow'):
            ensure_extension_schema(conn)
        assert conn.execute("SELECT to_regclass('kraken_research_evidence_extension') AS r").fetchone()['r'] is None


def test_original_evidence_and_brain_preserved_across_10000_handoff(pg, monkeypatch):
    from test_kraken_research_overflow import prepare_tables
    from database import CANONICAL_PROTECTED_TABLES, DATABASE_RETENTION_POLICIES
    activate(monkeypatch)
    assert EXTENSION_TABLE in CANONICAL_PROTECTED_TABLES
    assert EXTENSION_TABLE not in DATABASE_RETENTION_POLICIES
    result = research_forecast(quote(), history(), NOW, forecast=prediction)
    with pg() as conn:
        prepare_tables(conn)
        assert ensure_overflow_schema(conn)
        conn.execute('INSERT INTO kraken_research_evidence VALUES(5000,%s,%s,%s::jsonb,%s)',
                     ('old-fixture', NOW, json.dumps(result), b'legacy-bytes'))
        conn.execute('INSERT INTO kraken_research_evidence_overflow VALUES(10000,%s,%s,%s::jsonb,%s)',
                     ('overflow-fixture', NOW, json.dumps(result), b'overflow-bytes'))
        assert sync_research_sources(conn)['inserted'] == 2
        original = conn.execute('SELECT * FROM kraken_research_evidence').fetchall()
        overflow = conn.execute('SELECT * FROM kraken_research_evidence_overflow').fetchall()
        brain = conn.execute('SELECT * FROM oracle_brain_sources ORDER BY source_key').fetchall()
        assert read_evidence_capacity(conn)['status'] == 'CAPACITY_REACHED'
        assert ensure_extension_schema(conn)
        assert ensure_extension_schema(conn)
        assert read_evidence_capacity(conn)['remaining_id_slots'] == 5000
        other = {**result, 'forecast': {**result['forecast'],
                 'model_version': result['forecast']['model_version'] + '-extension-fixture'}}
        assert persist_evidence(conn, other, history()) == 10001
        assert sync_research_sources(conn)['inserted'] == 1
        receipt = committed_receipt(conn)
        assert (receipt['expected'], receipt['matched'], receipt['unmatched']) == (3, 3, 0)
        assert receipt['evidence_capacity']['max_id'] == 15000
        assert receipt['storage_footprint']['extension_bytes'] > 0
        source = conn.execute("SELECT source_ref,metadata FROM oracle_brain_sources WHERE source_key='kraken-research:10001'").fetchone()
        assert source['source_ref'] == EXTENSION_TABLE + ':10001'
        assert source['metadata']['trade_outcome'] is False
        assert conn.execute('SELECT * FROM kraken_research_evidence').fetchall() == original
        assert conn.execute('SELECT * FROM kraken_research_evidence_overflow').fetchall() == overflow
        assert conn.execute("SELECT * FROM oracle_brain_sources WHERE source_key IN ('kraken-research:5000','kraken-research:10000') ORDER BY source_key").fetchall() == brain
        monkeypatch.setenv('KRAKEN_RESEARCH_EXTENSION_CREATE_ENABLED', 'false')
        assert ensure_extension_schema(conn) is False
        assert committed_receipt(conn)['matched'] == 3


def test_extension_last_slot_fails_closed_without_rewriting(pg, monkeypatch):
    activate(monkeypatch)
    result = research_forecast(quote(), history(), NOW, forecast=prediction)
    with pg() as conn:
        conn.execute(EVIDENCE_DDL)
        assert ensure_overflow_schema(conn)
        assert ensure_extension_schema(conn)
        conn.execute('INSERT INTO kraken_research_evidence_extension VALUES(15000,%s,%s,%s::jsonb,%s)',
                     ('last-fixture', NOW, json.dumps(result), b'last-original-bytes'))
        before = conn.execute('SELECT * FROM kraken_research_evidence_extension').fetchall()
        assert read_evidence_capacity(conn)['status'] == 'CAPACITY_REACHED'
        with pytest.raises(ResearchUnavailable, match='CAPACITY_REACHED'):
            persist_evidence(conn, {**result, 'symbol': 'ETH-USD'}, history())
        assert conn.execute('SELECT * FROM kraken_research_evidence_extension').fetchall() == before


@pytest.mark.parametrize('bad_id', [10000, 15001])
def test_extension_check_constraint_rejects_wrong_id(pg, monkeypatch, bad_id):
    import psycopg
    activate(monkeypatch)
    with pg() as conn:
        conn.execute(EVIDENCE_DDL)
        assert ensure_overflow_schema(conn)
        assert ensure_extension_schema(conn)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute('INSERT INTO kraken_research_evidence_extension VALUES(%s,%s,%s,%s::jsonb,%s)',
                         (bad_id, 'bad-fixture', NOW, '{}', b''))
        assert conn.execute('SELECT count(*) AS n FROM kraken_research_evidence_extension').fetchone()['n'] == 0


def test_extension_static_sql_projection():
    layout = {'tables': ('kraken_research_evidence','kraken_research_evidence_overflow',
                         'kraken_research_evidence_extension')}
    assert evidence_sql(layout, 'id').count('UNION ALL') == 2
    with pytest.raises(ValueError):
        evidence_sql(layout, 'id;DROP TABLE oracle_brain_sources')
