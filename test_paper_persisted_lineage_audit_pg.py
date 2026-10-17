"""Actual PostgreSQL migration compatibility and read-only aggregate boundaries.

Synthetic fixtures use the existing guarded local UUID-schema fixture. No
production connection or historical evidence is modified.
"""
import json
from pathlib import Path

import pytest

from paper_persisted_lineage_audit import summarize_persisted_lineage
from test_paper_fill_provenance_pg import fill_evidence_db


@pytest.fixture
def lineage_db(fill_evidence_db):
    connection, _ = fill_evidence_db
    with connection() as conn:
        for filename in ('20260919_oracle_brain.sql', '20260920_oracle_brain_learning_v2.sql', '20260922_oracle_advanced_learning.sql'):
            conn.execute((Path(__file__).parent/'migrations'/filename).read_text())
    return connection


def fill(conn, name, fee, market='crypto', linked=False):
    conn.execute("""INSERT INTO paper_orders(order_id,market,symbol,side,status,created_at,updated_at)
        VALUES (%s,%s,'TEST-USD','BUY','FILLED','2026-10-16T00:00:00Z','2026-10-16T00:00:00Z')""", (name,market))
    execution = None
    if linked:
        execution = conn.execute("""INSERT INTO trades(market,symbol,side,quantity,price,created_at)
            VALUES (%s,'TEST-USD','BUY',1,100,'2026-10-16T00:00:00Z') RETURNING id""", (market,)).fetchone()['id']
    conn.execute("""INSERT INTO paper_fills(fill_id,order_id,market,symbol,side,quantity,
        fill_price,notional,fee_amount,created_at,canonical_trade_id)
        VALUES (%s,%s,%s,'TEST-USD','BUY',1,100,100,%s,'2026-10-16T00:00:00Z',%s)""",
        (name,name,market,fee,execution))


def seed(conn):
    for index, fee in enumerate((0, .25, -.1, float('nan'), float('inf'), float('-inf'))):
        fill(conn, 'crypto-'+str(index), fee, linked=index == 0)
    fill(conn, 'cash-fill', .1, market='cash', linked=True)
    for key, market, status, accounting in [('good','crypto','exact','exact_round_trip_v1'),
        ('partial','crypto','partial','exact_round_trip_v1'), ('legacy','crypto','exact','legacy'),
        ('cash','cash','exact','exact_round_trip_v1')]:
        conn.execute("""INSERT INTO oracle_brain_episodes(episode_key,trade_id,market,symbol,strategy,
            regime,provenance_status,outcome_snapshot) VALUES (%s,%s,%s,'TEST-USD','test','range',%s,%s::jsonb)""",
            (key,key,market,status,json.dumps({'accounting_version':accounting,'cost_provenance':'exact_lot'})))
    for key, market in [(1,'crypto'),(2,'crypto'),(3,'cash')]:
        conn.execute("""INSERT INTO oracle_decision_replays(decision_id,market,symbol,decision_time,lineage_hash)
            VALUES (%s,%s,'TEST-USD',NOW(),'synthetic-test')""", (key,market))


def test_actual_schema_counts_market_isolation_and_nonfinite_fees(lineage_db):
    with lineage_db() as conn: seed(conn)
    with lineage_db() as reader:
        crypto = summarize_persisted_lineage(reader)
    assert crypto['paper_fills'] == {'total':6,'linked':1,'isolated':5,'invalid_fee':4}
    assert crypto['exact_brain_episodes'] == 1
    assert crypto['retrospective_decision_replays'] == 2
    assert crypto['verified_decision_consumption'] == 'UNAVAILABLE'
    with lineage_db() as reader:
        cash = summarize_persisted_lineage(reader,market='cash')
    assert cash['paper_fills'] == {'total':1,'linked':1,'isolated':0,'invalid_fee':0}
    assert cash['exact_brain_episodes'] == 1
    assert cash['retrospective_decision_replays'] == 1


def test_reader_is_repeatable_read_and_rejects_actual_writes(lineage_db):
    import psycopg
    with lineage_db() as conn: seed(conn)
    with lineage_db() as reader:
        summarize_persisted_lineage(reader)
        assert reader.execute('SHOW transaction_read_only').fetchone()['transaction_read_only'] == 'on'
        assert reader.execute('SHOW transaction_isolation').fetchone()['transaction_isolation'] == 'repeatable read'
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            reader.execute("UPDATE paper_fills SET fee_amount=99")
        reader.rollback()
    with lineage_db() as reader:
        assert summarize_persisted_lineage(reader)['paper_fills']['invalid_fee'] == 4


def test_uncommitted_rows_excluded_and_snapshot_stays_consistent(lineage_db):
    with lineage_db() as conn: seed(conn)
    with lineage_db() as writer, lineage_db() as reader:
        fill(writer, 'pending-fill', .1)
        assert summarize_persisted_lineage(reader)['paper_fills']['total'] == 6
        writer.commit()
        assert reader.execute("SELECT count(*) AS n FROM paper_fills WHERE market='crypto'").fetchone()['n'] == 6
    with lineage_db() as reader:
        assert summarize_persisted_lineage(reader)['paper_fills']['total'] == 7


def test_empty_audit_never_infers_learning_or_profit(lineage_db):
    with lineage_db() as reader:
        result = summarize_persisted_lineage(reader)
    assert result['paper_fills'] == {'total':0,'linked':0,'isolated':0,'invalid_fee':0}
    assert result['exact_brain_episodes'] == result['retrospective_decision_replays'] == 0
    assert result['verified_decision_consumption'] == 'UNAVAILABLE'
    assert result['historical_repair'] == result['execution_impact'] == 'NONE'
