"""Actual disposable PostgreSQL persistence; all prices/forecasts are synthetic."""
from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock
import json
import threading
import pytest

import prospective_paper_research as pilot
from test_kraken_reference_pg import pg
from test_kraken_research_overflow import prepare_tables
from test_kraken_reference_book import NOW
from test_kraken_paper_pricing import provider
from test_prospective_paper_research import trial, exit_book, result
from kraken_research_store import OVERFLOW_DDL


def seed(conn,identity,t):
    table='kraken_research_evidence' if identity<=5000 else 'kraken_research_evidence_overflow'
    conn.execute(f'INSERT INTO {table} VALUES(%s,%s,%s,%s::jsonb,%s)',
        (identity,'original-'+str(identity),NOW,json.dumps({**result(),'forward_trial':t}),b'original-history'))


def setup(conn,highest=0,maximum=5000):
    prepare_tables(conn)
    if maximum==10000:conn.execute(OVERFLOW_DDL)
    return pilot.pilot_config(conn,{'highest_id':highest,'max_id':maximum},NOW)


def test_pg_frozen_remaining_window_no_reset_or_historical_backfill(pg):
    with pg() as conn:
        config=setup(conn,9373,10000)
        assert config['first_source_id']==9374 and config['last_source_id']==10000
        assert pilot.pilot_config(conn,{'highest_id':10000,'max_id':10000},NOW+timedelta(days=1))==config
        source=conn.execute('SELECT metadata,execution_impact FROM oracle_brain_sources').fetchone()
        assert source['execution_impact']=='NONE' and source['metadata']['ranking_eligible'] is False
    with pg() as reader:
        assert reader.execute('SELECT count(*) AS n FROM oracle_brain_sources').fetchone()['n']==1


def test_pg_original_entry_two_outcomes_atomic_idempotent_and_context_only(pg,monkeypatch):
    t=trial(monkeypatch);at=NOW+timedelta(seconds=300)
    p=Mock();p.clock.return_value=at;p.quote.return_value=exit_book(at);p.authentic.return_value=True
    with pg() as conn:
        config=setup(conn);seed(conn,1,t)
        original=conn.execute('SELECT * FROM kraken_research_evidence').fetchone()
        with conn.transaction():assert pilot.evaluate_pending(conn,p,config,at)==1
        assert pilot.evaluate_pending(conn,p,config,at)==0
        assert p.quote.call_count==1  # No repeated pricing request for a finalized horizon.
        p.clock.return_value=NOW+timedelta(seconds=900);p.quote.return_value=exit_book(p.clock())
        assert pilot.evaluate_pending(conn,p,config,p.clock())==1
        assert conn.execute('SELECT * FROM kraken_research_evidence').fetchone()==original
    with pg() as reader:
        rows=reader.execute("SELECT * FROM oracle_brain_sources WHERE source_type='crypto_forward_paper_simulation' AND symbol IS NOT NULL").fetchall()
        assert len(rows)==2
        assert all(r['confidence']==0 and r['execution_impact']=='NONE' and r['category']=='market_research' for r in rows)
        assert all(r['metadata']['context_only'] and r['metadata']['ranking_eligible'] is False
                   and r['metadata']['trade_outcome'] is False and r['metadata']['canonical_trade'] is False for r in rows)
        summary=pilot.summarize(reader,config)
        assert summary['matched_simulated_entries']==1 and summary['distinct_entry_observations']==1, [(r['metadata'].get('status'),r['metadata'].get('reason')) for r in rows]
        assert summary['canonical_shadow_exit_pairs']==0


def test_pg_rollback_does_not_leave_partial_outcome(pg,monkeypatch):
    t=trial(monkeypatch);at=NOW+timedelta(seconds=300)
    p=Mock();p.clock.return_value=at;p.quote.return_value=exit_book(at);p.authentic.return_value=True
    with pg() as conn:
        config=setup(conn);seed(conn,1,t)
        with pytest.raises(RuntimeError):
            with conn.transaction():
                assert pilot.evaluate_pending(conn,p,config,at)==1
                raise RuntimeError('synthetic transaction failure')
    with pg() as reader:
        assert reader.execute("SELECT count(*) AS n FROM oracle_brain_sources WHERE symbol IS NOT NULL").fetchone()['n']==0
        assert pilot.evaluate_pending(reader,p,config,at)==1


def test_pg_bad_exit_retries_then_gap_expires_without_price(pg,monkeypatch):
    t=trial(monkeypatch);at=NOW+timedelta(seconds=300)
    p=Mock();p.clock.return_value=at;p.quote.return_value={**exit_book(at),'quote_id':'tampered'};p.authentic.return_value=True
    with pg() as conn:
        config=setup(conn);seed(conn,1,t)
        assert pilot.evaluate_pending(conn,p,config,at)==0
        p.clock.return_value=NOW+timedelta(seconds=361)
        assert pilot.evaluate_pending(conn,p,config,p.clock())==1
        value=conn.execute('SELECT metadata FROM oracle_brain_sources WHERE symbol IS NOT NULL').fetchone()['metadata']
        assert value['status']=='UNAVAILABLE' and 'net_pnl' not in value
        assert value['reason']=='MISSED_PREDECLARED_EXIT_WINDOW'


def test_pg_concurrent_finalization_never_duplicates(pg,monkeypatch):
    t=trial(monkeypatch);at=NOW+timedelta(seconds=300)
    with pg() as conn:config=setup(conn);seed(conn,1,t)
    barrier=threading.Barrier(2);errors=[];counts=[]
    def run():
        try:
            p=Mock();p.clock.return_value=at;p.quote.return_value=exit_book(at);p.authentic.return_value=True
            with pg() as conn:
                barrier.wait(timeout=5)
                with conn.transaction():counts.append(pilot.evaluate_pending(conn,p,config,at))
        except Exception as exc:errors.append(exc)
    threads=[threading.Thread(target=run) for _ in range(2)]
    for thread in threads:thread.start()
    for thread in threads:thread.join(timeout=10)
    assert not errors and not any(thread.is_alive() for thread in threads)
    assert sum(counts)==1
    with pg() as reader:assert reader.execute('SELECT count(*) AS n FROM oracle_brain_sources WHERE symbol IS NOT NULL').fetchone()['n']==1


def test_pg_full_source_storage_still_finalizes_original_trials(pg,monkeypatch):
    import kraken_primary_research as research
    t=trial(monkeypatch);at=NOW+timedelta(seconds=961)
    monkeypatch.setenv('KRAKEN_FORWARD_PAPER_PILOT_ENABLED','true')
    p=Mock();p.clock.return_value=at
    with pg() as conn:
        config=setup(conn,4999);seed(conn,5000,t)
        # Evaluating pending observations does not allocate a new source ID.
        assert pilot.evaluate_pending(conn,p,config,at)==2
        assert conn.execute('SELECT max(id) AS n FROM kraken_research_evidence').fetchone()['n']==5000
        p.quote.assert_not_called()
        values=conn.execute('SELECT metadata FROM oracle_brain_sources WHERE symbol IS NOT NULL').fetchall()
        assert all(v['metadata']['status']=='UNAVAILABLE' for v in values)


def test_pg_worker_captures_once_then_full_capacity_evaluates_without_new_history(pg,monkeypatch):
    import kraken_primary_research as research
    import kraken_reference_runtime as runtime
    from types import SimpleNamespace
    from test_kraken_primary_research import history
    p,snapshot=provider(monkeypatch)
    monkeypatch.setenv('KRAKEN_FORWARD_PAPER_PILOT_ENABLED','true')
    current=[NOW]
    p.clock=lambda:current[0]
    monkeypatch.setattr(research,'datetime',SimpleNamespace(now=lambda *_:current[0]))
    monkeypatch.setattr(runtime,'read_kraken_reference_state',lambda:deepcopy(snapshot))
    # Forecast generation is mocked, but the actual adapter validates the book;
    # actual worker, immutable source INSERT, and outcome INSERT run against PG.
    r={**result(),'history_provider':'Kraken Public OHLC','history_last_closed_bar':NOW.isoformat()}
    monkeypatch.setattr(research,'research_forecast',lambda *_:deepcopy(r))
    monkeypatch.setattr(research,'coinbase_quote',Mock(side_effect=ValueError('synthetic unavailable comparison')))
    data=SimpleNamespace(history=Mock(return_value=history()))
    worker=research.PrimaryResearch(data=data,paper_pricing=p)
    with pg() as conn:
        prepare_tables(conn);conn.execute(research.DDL)
        conn.execute("INSERT INTO kraken_research_evidence VALUES(4999,'untouched',%s,'{}',%s)",(NOW,b'old'))
        worker.cycle(conn)
        captured=conn.execute('SELECT payload FROM kraken_research_evidence WHERE id=5000').fetchone()['payload']
        assert captured['forward_trial']['status']=='CAPTURED'
        assert data.history.call_count==1
        current[0]=NOW+timedelta(seconds=300)
        q=exit_book(current[0]);snapshot['quotes']['BTC-USD']=q['original_reference']
        # The real adapter refreshes its expired metadata cache from the mocked
        # public product metadata response, never from production.
        worker.cycle(conn)
        assert data.history.call_count==1
        state=conn.execute('SELECT payload FROM kraken_research_state').fetchone()['payload']
        assert state['status']=='EVIDENCE_CAPACITY_REACHED'
        assert state['prospective_paper']['horizons']['300']['completed_simulations']==1
        assert conn.execute('SELECT payload FROM kraken_research_evidence WHERE id=5000').fetchone()['payload']==captured


@pytest.mark.parametrize('changed',[{'symbol':'ETH-USD'},{'history_fingerprint':'another-history'},
    {'forecast':{'forecast_id':'late'}},{'status':'SHADOW_FORECAST'}])
def test_pg_conflicting_parent_cannot_certify_a_simulated_outcome(pg,monkeypatch,changed):
    t=trial(monkeypatch);at=NOW+timedelta(seconds=300)
    p=Mock();p.clock.return_value=at
    with pg() as conn:
        config=setup(conn)
        conn.execute('INSERT INTO kraken_research_evidence VALUES(1,%s,%s,%s::jsonb,%s)',
            ('conflicting-parent',NOW,json.dumps({**result(),**changed,'forward_trial':t}),b'original'))
        assert pilot.evaluate_pending(conn,p,config,at)==2
        p.quote.assert_not_called()
        values=conn.execute('SELECT metadata FROM oracle_brain_sources WHERE symbol IS NOT NULL').fetchall()
        assert all(v['metadata']['status']=='UNAVAILABLE' and 'net_pnl' not in v['metadata'] for v in values)
