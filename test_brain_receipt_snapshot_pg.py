"""Receipt snapshots use disposable PostgreSQL and synthetic original evidence."""
import json

import pytest

from kraken_brain_evidence import committed_receipt, sync_research_sources
from kraken_primary_research import persist_evidence, research_forecast
from test_kraken_primary_research import history, quote, prediction
from test_kraken_reference_book import NOW
from test_kraken_reference_pg import pg
from test_kraken_research_overflow import prepare_tables


def seed(conn, identity):
    payload = research_forecast(quote(), history(), NOW, forecast=prediction)
    conn.execute("INSERT INTO kraken_research_evidence VALUES(%s,%s,%s,%s::jsonb,%s)",
                 (identity, 'snapshot-' + str(identity), NOW, json.dumps(payload), b'original'))
    sync_research_sources(conn)


def test_receipt_counts_and_sample_share_one_snapshot_during_committed_ingestion(pg):
    with pg() as reader, pg() as writer:
        prepare_tables(reader)
        seed(reader, 1)

        class ConcurrentIngestion:
            fired = False

            def execute(self, sql, *args):
                cursor = reader.execute(sql, *args)
                # The first receipt data statement has already taken its snapshot.
                # A different connection commits the next original and its receipt.
                if not self.fired and ('COUNT(*) AS matched' in sql or 'WITH checked AS MATERIALIZED' in sql):
                    self.fired = True
                    seed(writer, 2)
                return cursor

        proxy = ConcurrentIngestion()
        result = committed_receipt(proxy)
        assert proxy.fired
        assert result['expected'] == result['matched'] == 1
        assert result['unmatched'] == 0
        assert [r['id'] for r in result['sample']] == [1]
        assert committed_receipt(reader)['expected'] == 2


@pytest.mark.parametrize('corruption', [
    "DELETE FROM oracle_brain_sources WHERE source_key='kraken-research:2'",
    "UPDATE oracle_brain_sources SET metadata=jsonb_set(metadata,'{evidence,reason}','\"changed\"') WHERE source_key='kraken-research:2'",
    "UPDATE oracle_brain_sources SET source_ref='wrong:2' WHERE source_key='kraken-research:2'",
    "UPDATE oracle_brain_sources SET metadata=jsonb_set(metadata,'{source_table}','\"wrong\"') WHERE source_key='kraken-research:2'",
    "UPDATE oracle_brain_sources SET metadata=jsonb_set(metadata,'{evidence_id}','3') WHERE source_key='kraken-research:2'",
    "UPDATE oracle_brain_sources SET metadata=metadata-'execution_authority' WHERE source_key='kraken-research:2'",
])
def test_snapshot_receipt_preserves_mismatch_checks_and_originals(pg, corruption):
    with pg() as conn:
        prepare_tables(conn)
        seed(conn, 1)
        seed(conn, 2)
        conn.execute(corruption)
        originals = conn.execute('SELECT * FROM kraken_research_evidence ORDER BY id').fetchall()
        sources = conn.execute('SELECT * FROM oracle_brain_sources ORDER BY source_key').fetchall()
        result = committed_receipt(conn)
        assert (result['expected'], result['matched'], result['unmatched']) == (2, 1, 1)
        assert [r['id'] for r in result['sample']] == [1]
        assert conn.execute('SELECT * FROM kraken_research_evidence ORDER BY id').fetchall() == originals
        assert conn.execute('SELECT * FROM oracle_brain_sources ORDER BY source_key').fetchall() == sources
        assert result['trade_outcomes'] == 'NOT_INFERRED'


def test_empty_snapshot_receipt(pg):
    with pg() as conn:
        prepare_tables(conn)
        result = committed_receipt(conn)
        assert (result['expected'], result['matched'], result['unmatched'], result['sample']) == (0, 0, 0, [])
