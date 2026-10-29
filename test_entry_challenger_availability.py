"""Late-created or originally incomplete learning records cannot enter a prior."""
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
import pytest
import paper_entry_edge_challenger_shadow as challenger
import paper_regime_economics_shadow as regime
from test_kraken_reference_pg import pg


@pytest.mark.parametrize('kind', ['ready','late','originally_incomplete'])
def test_pg_prior_uses_only_original_entry_ready_records_available_before_entry(pg,monkeypatch,kind):
    import database
    @contextmanager
    def transaction():
        with pg() as c:
            with c.transaction():yield c
    monkeypatch.setattr(database,'connect',transaction)
    monkeypatch.setattr(challenger,'active',lambda:True)
    monkeypatch.setattr(regime,'active',lambda:True)
    challenger.ensure_schema()
    anchor=datetime(2026,10,29,tzinfo=timezone.utc)
    with pg() as c:
        c.execute('CREATE TABLE trade_ledger(trade_id text,market text,quantity double precision,entry_price double precision)')
        c.execute('UPDATE paper_entry_edge_challenger_epoch SET started_at=%s WHERE version=%s',(anchor-timedelta(minutes=30),challenger._VERSION))
        for i in range(25):
            identity='prior-fixture-'+str(i)
            c.execute("INSERT INTO trade_ledger VALUES(%s,'crypto',1,100)",(identity,))
            c.execute('''INSERT INTO paper_regime_trade_metrics(trade_id,market,symbol,strategy,regime,entry_pattern,
              entry_time,exit_time,round_trip_net_pnl,round_trip_fees,cost_provenance,mfe_pct,mae_pct,excursion_sample_count,schema_version,created_at)
              VALUES(%s,'crypto','BTC-USD','oracle_council_v3','fixture','fixture',%s,%s,%s,.02,'exact_lot',2,-.1,2,%s,%s)''',
              (identity,anchor-timedelta(hours=2),anchor-timedelta(hours=1),-.1 if i==0 else 1,
               regime._SCHEMA_VERSION if kind=='originally_incomplete' else regime._ENTRY_READY_VERSION,
               anchor+timedelta(minutes=5) if kind=='late' else anchor-timedelta(minutes=59)))
        c.execute("INSERT INTO trade_ledger VALUES('candidate-fixture','crypto',1,100)")
        c.execute('''INSERT INTO paper_regime_trade_metrics(trade_id,market,symbol,strategy,regime,entry_pattern,
          entry_time,exit_time,round_trip_net_pnl,round_trip_fees,cost_provenance,schema_version)
          VALUES('candidate-fixture','crypto','BTC-USD','oracle_council_v3','fixture','fixture',%s,%s,2,.02,'exact_lot',%s)''',
          (anchor,anchor+timedelta(hours=1),regime._SCHEMA_VERSION))
        # Preserve an older experiment generation alongside the new one.
        c.execute("INSERT INTO paper_entry_edge_challenger_epoch VALUES('preserved-legacy-fixture',%s)",(anchor-timedelta(days=1),))
        assert challenger.evaluate_new_closes()==1
        result=c.execute("SELECT prior_samples,would_trade,prior_expectancy,prior_profit_factor,edge_score FROM paper_entry_edge_challenger_results WHERE trade_id='candidate-fixture' AND version=%s",(challenger._VERSION,)).fetchone()
        assert result['prior_samples']==(25 if kind=='ready' else 0)
        assert result['would_trade'] is (kind=='ready')
        if kind!='ready':
            assert result['prior_expectancy'] is None and result['prior_profit_factor'] is None
            assert result['edge_score'] is None
        assert challenger.evaluate_new_closes()==0
        assert c.execute("SELECT count(*) AS n FROM paper_entry_edge_challenger_epoch WHERE version='preserved-legacy-fixture'").fetchone()['n']==1
