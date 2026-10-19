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


from test_paper_fill_provenance_pg import entry_audit_db


def _seed_retention_candidates(conn):
    conn.execute('ALTER TABLE forecasts ADD COLUMN id BIGSERIAL')
    # Exercise independent lot-only and ledger-only references.
    conn.execute("UPDATE trade_ledger SET entry_signal_id=NULL,entry_forecast_id=NULL WHERE entry_signal_id='1'")
    conn.execute("UPDATE position_lots SET entry_signal_id=NULL,entry_forecast_id=NULL WHERE entry_signal_id='2'")
    conn.execute("INSERT INTO signals VALUES(3,'crypto','PARENT','2026-01-01T00:00:00Z')")
    conn.execute("INSERT INTO forecasts(forecast_id,signal_id,market,symbol,created_at,source_quote_timestamp) VALUES('parent-only',3,'crypto','PARENT','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')")
    for i in range(10):
        conn.execute("INSERT INTO signals VALUES(%s,'crypto','UNLINKED','2026-01-01T00:00:00Z')",(10+i,))
        conn.execute("INSERT INTO forecasts(forecast_id,signal_id,market,symbol,created_at,source_quote_timestamp) VALUES(%s,NULL,'crypto','UNLINKED','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')",('unlinked-'+str(i),))


def test_retention_preserves_exact_entry_parents_and_bounds_only_unlinked_rows(entry_audit_db):
    import database
    with entry_audit_db() as conn:_seed_retention_candidates(conn)
    policy={'keep_rows':2,'batch_size':2}
    with entry_audit_db() as conn:
        assert database._apply_retention_policy(conn,'signals',policy,max_batches=10)==8
        ids={r['id'] for r in conn.execute('SELECT id FROM signals').fetchall()}
        assert ids=={1,2,3,18,19}  # Two trade parents, one forecast parent, two unlinked.
        assert database._apply_retention_policy(conn,'forecasts',policy,max_batches=10)==9
        ids={r['forecast_id'] for r in conn.execute('SELECT forecast_id FROM forecasts').fetchall()}
        assert ids=={'forecast:lot-a','forecast:lot-b','unlinked-8','unlinked-9'}


@pytest.mark.parametrize('finish',['commit','rollback'])
def test_retention_skips_active_entry_writer_and_lock_releases(entry_audit_db,finish):
    import database
    with entry_audit_db() as conn:_seed_retention_candidates(conn)
    with entry_audit_db() as writer, entry_audit_db() as maintainer:
        writer.execute('SELECT pg_advisory_xact_lock_shared(%s)',(database.ENTRY_EVIDENCE_RETENTION_LOCK,))
        assert database._apply_retention_policy(maintainer,'signals',{'keep_rows':2,'batch_size':2})==0
        assert maintainer.execute('SELECT count(*) AS n FROM signals').fetchone()['n']==13
        getattr(writer,finish)()
        assert database._apply_retention_policy(maintainer,'signals',{'keep_rows':2,'batch_size':2})==8
        assert maintainer.execute('SELECT count(*) AS n FROM signals').fetchone()['n']==5


@pytest.fixture
def pricing_receipt_db(fill_evidence_db):
    from test_paper_pricing_receipt_audit import receipt_graph
    connection, _ = fill_evidence_db
    stored_fill, receipt, event, entry = receipt_graph()
    with connection() as conn:
        conn.execute((Path(__file__).parent/'migrations/20261019_coinbase_paper_price_evidence.sql').read_text())
        fill(conn, 'fill-1', stored_fill['fee_amount'], linked=True)
        conn.execute('''UPDATE paper_fills SET symbol=%s,quote_provider=%s,quote_timestamp=%s,
            created_at=%s,fill_price=%s,notional=%s,fee_pct=%s,reference_price=%s,
            slippage_pct=%s,latency_pct=%s,market_impact_pct=%s WHERE fill_id='fill-1' ''',
            tuple(stored_fill[k] for k in ('symbol','quote_provider','quote_timestamp','created_at',
                'fill_price','notional','fee_pct','reference_price','slippage_pct','latency_pct','market_impact_pct')))
        conn.execute('UPDATE trades SET created_at=%s', (event.isoformat(),))
    return connection, receipt, entry


def _write_pricing_receipt(conn, receipt):
    conn.execute('''INSERT INTO paper_fill_price_evidence
        (fill_id,model_version,quote_id,evidence,created_at,execution_authority)
        VALUES(%s,%s,%s,%s::jsonb,%s,%s)''',
        (receipt['fill_id'],receipt['model_version'],receipt['quote_id'],json.dumps(receipt['evidence']),
         receipt['created_at'],receipt['execution_authority']))


def _read_pricing_receipt_audit(conn, entry):
    from paper_pricing_receipt_audit import audit_sample_pricing_receipts
    conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    record = conn.execute('''SELECT to_jsonb(f) AS fill,to_jsonb(t) AS execution
        FROM paper_fills f JOIN trades t ON t.id=f.canonical_trade_id WHERE f.fill_id='fill-1' ''').fetchone()
    return audit_sample_pricing_receipts(conn, [{**dict(record), 'ledger':entry}])


def test_pricing_audit_reads_only_committed_original_receipts(pricing_receipt_db):
    connection, receipt, entry = pricing_receipt_db
    with connection() as writer, connection() as reader:
        _write_pricing_receipt(writer,receipt)
        report = _read_pricing_receipt_audit(reader,entry)
        assert report['consistent_original_receipts']==0
        assert report['records'][0]['reasons']==['missing_pricing_receipt']
        writer.commit()
        # A committed-later row cannot enter the reader's original snapshot.
        assert reader.execute('SELECT COUNT(*) AS n FROM paper_fill_price_evidence').fetchone()['n']==0
    with connection() as reader:
        report = _read_pricing_receipt_audit(reader,entry)
        assert report['sample_fills']==report['consistent_original_receipts']==1
        assert report['records'][0]['fee_provenance']=='MODELED_NOT_ACCOUNT_VERIFIED'
        assert reader.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']=='on'


@pytest.mark.parametrize('conflict',['late','wrong_quote','wrong_symbol','wrong_cost','late_reference','relabelled_reference'])
def test_persisted_late_and_conflicting_receipts_remain_unverified(pricing_receipt_db,conflict):
    from datetime import timedelta
    connection, receipt, entry = pricing_receipt_db
    if conflict=='late':receipt['created_at']+=timedelta(minutes=1)
    elif conflict=='wrong_quote':receipt['quote_id']='unrelated-quote'
    elif conflict=='wrong_symbol':receipt['evidence']['source_quote']['symbol']='ETH-USD'
    elif conflict=='late_reference':
        from kraken_reference_book import compare_reference
        quote=receipt['evidence']['source_quote']
        reference=quote['independent_comparison']['reference_quote']
        reference['received_at']=(entry['decision_timestamp']+timedelta(milliseconds=250)).isoformat()
        quote['independent_comparison']=compare_reference(reference,quote,receipt['created_at'])
    elif conflict=='relabelled_reference':
        receipt['evidence']['source_quote']['independent_comparison']['reference_quote']['provider']='Yahoo Finance'
    else:receipt['evidence']['execution']['fee_amount']=0
    with connection() as writer:_write_pricing_receipt(writer,receipt)
    with connection() as reader:
        report = _read_pricing_receipt_audit(reader,entry)
        assert report['sample_fills']==1 and report['consistent_original_receipts']==0
        assert report['records'][0]['reasons']


def test_complete_entry_references_are_not_fully_verified_trades_or_brain_consumption(entry_audit_db):
    from paper_trade_evidence_audit import summarize_trade_evidence
    with entry_audit_db() as reader:
        report=summarize_trade_evidence(reader,market='crypto')
        assert report['sample_complete_entry_references']==2
        assert report['sample_fully_verified_trades']==0
        assert report['full_trade_verification']['status']=='UNAVAILABLE'
        assert report['full_trade_verification']['finalized_round_trip_status']=='NOT_EVALUATED'
        assert report['decision_time_brain_consumption']['status']=='UNAVAILABLE'
        assert report['decision_time_brain_consumption']['ingestion_and_retrospective_replays_are_consumption_proof'] is False
        assert report['sample_pricing_receipts']['sample_fills']==4
        assert report['sample_pricing_receipts']['consistent_original_receipts']==0


@pytest.mark.parametrize('kind',['stored_before_decision','stored_late','conflicting_replay'])
def test_persisted_brain_records_cannot_stand_in_for_consumption_receipts(entry_audit_db,kind):
    from datetime import timedelta
    from paper_trade_evidence_audit import summarize_trade_evidence
    with entry_audit_db() as writer:
        for filename in ('20260919_oracle_brain.sql','20260920_oracle_brain_learning_v2.sql','20260922_oracle_advanced_learning.sql'):
            writer.execute((Path(__file__).parent/'migrations'/filename).read_text())
        row=writer.execute("SELECT decision_timestamp FROM trade_ledger WHERE trade_id='buy:lot-a'").fetchone()
        from paper_fill_provenance import _time
        decision=_time(row['decision_timestamp'])
        created=decision+timedelta(days=1) if kind=='stored_late' else decision-timedelta(seconds=1)
        writer.execute('''INSERT INTO oracle_brain_episodes
            (episode_key,trade_id,market,symbol,strategy,regime,provenance_status,outcome_snapshot,created_at)
            VALUES('trade:past','past','crypto','TEST-USD','test','test','exact',
            '{"accounting_version":"exact_round_trip_v1","cost_provenance":"exact_lot"}',%s)''',(created,))
        # Even a replay that claims consumption is retrospective, not an original
        # decision-time writer/commit receipt. No ID or timestamp inference.
        keys=['trade:other'] if kind=='conflicting_replay' else ['trade:past']
        writer.execute('''INSERT INTO oracle_decision_replays
            (decision_id,market,symbol,decision_time,replay_snapshot,lineage_hash)
            VALUES(1,'crypto','TEST-USD',%s,%s::jsonb,'test-only-replay')''',
            (decision,json.dumps({'consumed_episode_keys':keys,'evidence_immutable':True})))
    with entry_audit_db() as reader:
        report=summarize_trade_evidence(reader,market='crypto')
        assert report['decision_time_brain_consumption']['status']=='UNAVAILABLE'
        assert report['sample_fully_verified_trades']==0
        assert report['sample_complete_entry_references']==2
