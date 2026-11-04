"""Unchanged research cohorts must not create a fresh PostgreSQL row version."""
from pathlib import Path

import oracle_advanced_learning as learning
from test_kraken_reference_pg import pg


def prepare(conn):
    for migration, table in (('20260920_oracle_brain_learning_v2.sql','oracle_brain_episodes'),
                             ('20260922_oracle_advanced_learning.sql','oracle_setup_validation')):
        raw = Path('migrations', migration).read_text()
        start = raw.index('CREATE TABLE IF NOT EXISTS '+table+' (')
        end = raw.index('\n);', start) + 3
        conn.execute(raw[start:end])
    conn.execute("""INSERT INTO oracle_brain_episodes
        (episode_key,market,symbol,strategy,regime,entry_time,exit_time,net_pnl,fees,
         provenance_status,outcome_snapshot)
        VALUES('fixture','crypto','BTC-USD','fixture-strategy','range',
               NOW()-INTERVAL '2 hours',NOW()-INTERVAL '1 hour',-1,0.2,
               'exact','{"accounting_version":"exact_round_trip_v1","cost_provenance":"exact_lot"}')""")


def cohort(conn):
    return conn.execute("SELECT *,xmin::text AS row_version,ctid::text AS row_location FROM oracle_setup_validation").fetchone()


def test_identical_refresh_does_not_rewrite_cohort(pg):
    with pg() as conn:
        prepare(conn)
        assert learning.refresh_setup_attribution(conn, market='crypto') == {'setup_cohorts':1}
        original = cohort(conn)
        for _ in range(5):
            assert learning.refresh_setup_attribution(conn, market='crypto') == {'setup_cohorts':1}
            assert cohort(conn) == original
        assert conn.execute('SELECT count(*) AS n FROM oracle_brain_episodes').fetchone()['n'] == 1


def test_changed_real_outcome_refreshes_metrics_and_nullable_excursions(pg):
    with pg() as conn:
        prepare(conn)
        learning.refresh_setup_attribution(conn, market='crypto')
        original = cohort(conn)
        assert original['avg_mfe_pct'] is None
        # This mutation is confined to the disposable fixture, not historical production evidence.
        conn.execute("UPDATE oracle_brain_episodes SET net_pnl=2,mfe_pct=1.5 WHERE episode_key='fixture'")
        learning.refresh_setup_attribution(conn, market='crypto')
        changed = cohort(conn)
        assert changed['row_version'] != original['row_version']
        assert (changed['wins'], changed['losses'], changed['expectancy']) == (1,0,2)
        assert changed['profit_factor'] is None and changed['avg_mfe_pct'] == 1.5
        assert changed['updated_at'] > original['updated_at']
        learning.refresh_setup_attribution(conn, market='crypto')
        assert cohort(conn) == changed


def test_crypto_refresh_does_not_rewrite_other_market_or_mutate_source_episodes(pg):
    with pg() as conn:
        prepare(conn)
        conn.execute("""INSERT INTO oracle_brain_episodes
            (episode_key,market,symbol,strategy,regime,entry_time,exit_time,net_pnl,fees,
             provenance_status,outcome_snapshot)
            SELECT 'cash-fixture','cash','SPY',strategy,regime,entry_time,exit_time,1,fees,
                   provenance_status,outcome_snapshot FROM oracle_brain_episodes""")
        learning.refresh_setup_attribution(conn, market='cash')
        cash_before = conn.execute("SELECT *,xmin::text AS v FROM oracle_setup_validation WHERE market='cash'").fetchone()
        episodes = conn.execute('SELECT * FROM oracle_brain_episodes ORDER BY episode_key').fetchall()
        learning.refresh_setup_attribution(conn, market='crypto')
        assert conn.execute("SELECT *,xmin::text AS v FROM oracle_setup_validation WHERE market='cash'").fetchone() == cash_before
        assert conn.execute('SELECT * FROM oracle_brain_episodes ORDER BY episode_key').fetchall() == episodes
