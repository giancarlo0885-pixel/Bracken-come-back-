"""Execute the shadow producers against conflicting cash/crypto histories."""
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import json
import os
import re
import sqlite3

import pytest

import paper_aeve_generation_controller as aeve
import paper_entry_edge_challenger_shadow as entry
import paper_regime_economics_shadow as regime


START = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)


class _Result:
    def __init__(self, cursor=None):
        self.cursor = cursor

    def fetchall(self):
        records = [] if self.cursor is None else [dict(row) for row in self.cursor.fetchall()]
        for row in records:
            for field in ("feature_snapshot", "aeve_config_json"):
                if isinstance(row.get(field), str):
                    row[field] = json.loads(row[field])
        return records

    def fetchone(self):
        rows = self.fetchall()
        return rows[0] if rows else None


class _ResearchDB:
    """Run the real aggregation, temporal filters and writes, without PostgreSQL.

    The fixture has exactly one generation, so a normal join is equivalent to
    the producer's latest-started-generation LATERAL join. JSON casts, bind
    markers and the PostgreSQL advisory lock are the remaining adaptations.
    """
    def __init__(self, postgres_url=None):
        self.postgres = bool(postgres_url)
        if self.postgres:
            import psycopg
            from psycopg.rows import dict_row
            self.db = psycopg.connect(postgres_url, row_factory=dict_row)
        else:
            self.db = sqlite3.connect(":memory:")
            self.db.row_factory = sqlite3.Row
        ddl = """
            CREATE TABLE paper_regime_trade_metrics(
                trade_id TEXT,market TEXT,strategy TEXT,regime TEXT,entry_time TEXT,exit_time TEXT,
                round_trip_net_pnl REAL,round_trip_fees REAL,cost_provenance TEXT,
                mfe_pct REAL,mae_pct REAL,excursion_sample_count INTEGER,created_at TEXT,schema_version TEXT);
            CREATE TABLE trade_ledger(trade_id TEXT,market TEXT,quantity REAL,entry_price REAL,feature_snapshot TEXT);
            CREATE TABLE paper_aeve_generations(
                generation INTEGER,started_at TEXT,config_json TEXT,config_hash TEXT,status TEXT);
            CREATE TABLE paper_aeve_provenance_epochs(provenance_version INTEGER,started_at TEXT);
            CREATE TABLE paper_aeve_generation_outcomes(
                generation INTEGER,trade_id TEXT,observed_at TEXT,net_pnl REAL,mfe_pct REAL,mae_pct REAL,
                excursion_sample_count INTEGER,cost_pct REAL,would_trade INTEGER,score REAL,config_json TEXT,
                config_hash TEXT,provenance_version INTEGER,cost_provenance TEXT,
                entry_evidence_complete INTEGER,entry_time TEXT,UNIQUE(generation,trade_id));
            CREATE TABLE paper_entry_edge_challenger_epoch(version TEXT,started_at TEXT);
            CREATE TABLE paper_entry_edge_challenger_results(
                trade_id TEXT,version TEXT,strategy TEXT,regime TEXT,entry_time TEXT,exit_time TEXT,
                prior_samples INTEGER,prior_expectancy REAL,prior_profit_factor REAL,prior_mfe_pct REAL,
                prior_mae_pct REAL,prior_cost_pct REAL,prior_loss_streak INTEGER,edge_score REAL,
                would_trade INTEGER,actual_net_pnl REAL,avoided_loss REAL,missed_winner REAL,
                UNIQUE(version,trade_id));
        """
        if self.postgres:
            ddl = ddl.replace("CREATE TABLE", "CREATE TEMP TABLE")
            for field in ("entry_time", "exit_time", "observed_at", "started_at", "created_at"):
                ddl = ddl.replace(field + " TEXT", field + " TIMESTAMPTZ")
            for field in ("feature_snapshot", "config_json"):
                ddl = ddl.replace(field + " TEXT", field + " JSONB")
            for field in ("would_trade", "entry_evidence_complete"):
                ddl = ddl.replace(field + " INTEGER", field + " BOOLEAN")
            self.db.execute(ddl)
        else:
            self.db.executescript(ddl)

    def seed(self, sql, params=()):
        return self.db.execute(sql.replace("?", "%s") if self.postgres else sql, params)

    def execute(self, sql, params=()):
        if self.postgres:
            return _Result(self.db.execute(sql, params))
        if "pg_advisory_xact_lock" in sql:
            return _Result()
        sql = re.sub(
            r"JOIN LATERAL \(\s*SELECT generation,config_json,config_hash\s*"
            r"FROM paper_aeve_generations\s*WHERE started_at <= m.entry_time\s*"
            r"ORDER BY started_at DESC,generation DESC\s*LIMIT 1\s*\) g ON TRUE",
            "JOIN paper_aeve_generations g ON g.started_at <= m.entry_time", sql)
        sql = sql.replace("%s", "?").replace("::jsonb", "")
        values = tuple(value.isoformat() if isinstance(value, datetime) else value for value in (params or ()))
        return _Result(self.db.execute(sql, values))

    def add_trade(self, trade_id, market, pnl, exit_at, *, candidate=False):
        features = dict(expected_edge_pct=2, dip_depth_pct=.002, rebound_pct=.01, rsi_14=43,
                        aeve_estimated_round_trip_cost_pct=.1,
                        aeve_cost_estimated_at=(START-timedelta(minutes=1)).isoformat(),
                        aeve_cost_provenance="entry_signal_estimate")
        self.seed("INSERT INTO paper_regime_trade_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (trade_id, market, "oracle_council_v3", "range", START.isoformat() if candidate
                         else (exit_at-timedelta(hours=1)).isoformat(), exit_at.isoformat(),
                         pnl, .1, "exact_lot", 2, -.1, 5, (exit_at+timedelta(seconds=1)).isoformat(),
                         regime._ENTRY_READY_VERSION))
        self.seed("INSERT INTO trade_ledger VALUES (?,?,?,?,?)",
                        (trade_id, market, 1, 100, json.dumps(features)))


def _history(monkeypatch, market, postgres_url=None):
    import database
    conn = _ResearchDB(postgres_url)
    foreign_market = "cash" if market == "crypto" else "crypto"
    # The candidate's own market has two losses. The other market has enough
    # profitable history to flip both minimum-history and positive-EV gates.
    for index in range(2):
        conn.add_trade(f"own-{index}", market, -1, START-timedelta(minutes=20-index))
    for index in range(40):
        conn.add_trade(f"foreign-{index}", foreign_market, 1, START-timedelta(minutes=10, seconds=index))
    conn.add_trade("foreign-loss", foreign_market, -1, START-timedelta(minutes=30))
    conn.add_trade("candidate", market, .5, START+timedelta(hours=1), candidate=True)
    cfg = aeve.AEVEGenerationConfig()
    conn.seed("INSERT INTO paper_aeve_generations VALUES (?,?,?,?,?)",
                    (cfg.generation, (START-timedelta(days=2)).isoformat(), json.dumps(cfg.__dict__),
                     aeve.generation_config_hash(cfg), "ACTIVE"))
    conn.seed("INSERT INTO paper_aeve_provenance_epochs VALUES (?,?)",
                    (aeve.PROVENANCE_VERSION, START.isoformat()))
    conn.seed("INSERT INTO paper_entry_edge_challenger_epoch VALUES (?,?)",
                    (entry._VERSION, START.isoformat()))
    monkeypatch.setattr(database, "connect", lambda: nullcontext(conn))
    monkeypatch.setattr(aeve, "active", lambda: True)
    monkeypatch.setattr(entry, "active", lambda: True)
    return conn


def test_aeve_prior_samples_and_loss_streak_never_borrow_another_market(monkeypatch):
    import paper_aeve_v1_formula as formula
    conn = _history(monkeypatch, "crypto")
    captured = []
    real_score = formula.score_entry

    def capture(**kwargs):
        captured.append(kwargs)
        return real_score(**kwargs)

    monkeypatch.setattr(formula, "score_entry", capture)
    assert aeve.record_generation_outcomes() == 1
    assert captured[0]["min_samples"] == 2
    assert captured[0]["loss_streak"] == 2
    assert captured[0]["regime_expectancy_positive"] is False
    result = conn.db.execute("SELECT * FROM paper_aeve_generation_outcomes").fetchone()
    assert result["entry_evidence_complete"] == 1
    assert result["would_trade"] == 0
    assert conn.db.execute("SELECT COUNT(*) FROM paper_regime_trade_metrics").fetchone()[0] == 44


def test_entry_challenger_prior_economics_and_streak_stay_in_candidate_market(monkeypatch):
    conn = _history(monkeypatch, "crypto")
    assert entry.evaluate_new_closes() == 1
    result = conn.db.execute("SELECT * FROM paper_entry_edge_challenger_results").fetchone()
    assert result["prior_samples"] == 2
    assert result["prior_expectancy"] == -1
    assert result["prior_profit_factor"] == 0
    assert result["prior_loss_streak"] == 2
    assert result["would_trade"] == 0
    assert conn.db.execute("SELECT COUNT(*) FROM paper_regime_trade_metrics").fetchone()[0] == 44


def test_crypto_entrypoint_studies_do_not_evaluate_cash_candidates(monkeypatch):
    conn = _history(monkeypatch, "cash")
    assert aeve.record_generation_outcomes() == 0
    assert entry.evaluate_new_closes() == 0
    assert conn.db.execute("SELECT COUNT(*) FROM paper_aeve_generation_outcomes").fetchone()[0] == 0
    assert conn.db.execute("SELECT COUNT(*) FROM paper_entry_edge_challenger_results").fetchone()[0] == 0


def test_open_position_from_before_new_epoch_is_not_a_new_forward_entry(monkeypatch):
    conn = _history(monkeypatch, "crypto")
    conn.db.execute("UPDATE paper_regime_trade_metrics SET entry_time=? WHERE trade_id='candidate'",
                    ((START-timedelta(seconds=1)).isoformat(),))
    assert aeve.record_generation_outcomes() == 0
    assert entry.evaluate_new_closes() == 0


def test_mismatched_canonical_ledger_market_never_enters_crypto_studies(monkeypatch):
    conn = _history(monkeypatch, "crypto")
    conn.db.execute("UPDATE trade_ledger SET market='cash' WHERE trade_id='candidate'")
    assert aeve.record_generation_outcomes() == 0
    assert entry.evaluate_new_closes() == 0


def test_pre_epoch_historical_results_are_preserved_without_recertifying_them(monkeypatch):
    conn = _history(monkeypatch, "crypto")
    conn.db.execute("UPDATE paper_regime_trade_metrics SET entry_time=? WHERE trade_id='candidate'",
                    ((START-timedelta(seconds=1)).isoformat(),))
    identity = aeve.generation_config_hash(aeve.AEVEGenerationConfig())
    conn.seed("""INSERT INTO paper_aeve_generation_outcomes(
        generation,trade_id,config_hash,provenance_version,net_pnl,would_trade)
        VALUES (1,'candidate',?,7,99,TRUE)""", (identity,))
    conn.seed("""INSERT INTO paper_entry_edge_challenger_results(
        trade_id,version,actual_net_pnl,would_trade) VALUES
        ('candidate','entry-edge-challenger-v2-round-trip-cost',99,TRUE)""")
    before_aeve = [dict(row) for row in conn.db.execute("SELECT * FROM paper_aeve_generation_outcomes")]
    before_entry = [dict(row) for row in conn.db.execute("SELECT * FROM paper_entry_edge_challenger_results")]
    assert aeve.record_generation_outcomes() == 0
    assert entry.evaluate_new_closes() == 0
    assert [dict(row) for row in conn.db.execute("SELECT * FROM paper_aeve_generation_outcomes")] == before_aeve
    assert [dict(row) for row in conn.db.execute("SELECT * FROM paper_entry_edge_challenger_results")] == before_entry


@pytest.mark.parametrize("producer", ["aeve", "entry"])
def test_postgres_shadow_producers_isolate_crypto_priors_and_preserve_history(monkeypatch, producer):
    url = os.getenv("DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgres://")):
        pytest.skip("PostgreSQL integration service required")
    conn = _history(monkeypatch, "crypto", postgres_url=url)
    try:
        if producer == "aeve":
            import paper_aeve_v1_formula as formula
            captured, original = [], formula.score_entry

            def capture(**kwargs):
                captured.append(kwargs)
                return original(**kwargs)

            monkeypatch.setattr(formula, "score_entry", capture)
            assert aeve.record_generation_outcomes() == 1
            assert captured[0]["min_samples"] == 2
            assert captured[0]["loss_streak"] == 2
            result = conn.execute("SELECT * FROM paper_aeve_generation_outcomes").fetchone()
            assert result["provenance_version"] == aeve.PROVENANCE_VERSION
        else:
            assert entry.evaluate_new_closes() == 1
            result = conn.execute("SELECT * FROM paper_entry_edge_challenger_results").fetchone()
            assert result["prior_samples"] == 2
            assert result["prior_expectancy"] == -1
            assert result["prior_loss_streak"] == 2
            assert result["version"] == entry._VERSION
        assert result["would_trade"] is False
        assert conn.execute("SELECT COUNT(*) AS total FROM paper_regime_trade_metrics").fetchone()["total"] == 44
    finally:
        # All fixtures are connection-local temporary tables. No shared schema
        # or historical production records are created, modified or removed.
        conn.db.close()


@pytest.mark.parametrize('kind',['ready','late','originally_incomplete'])
@pytest.mark.parametrize('postgres',[False,True])
def test_aeve_prior_statistics_require_original_readiness_and_pre_entry_availability(monkeypatch,kind,postgres,request):
    import paper_aeve_v1_formula as formula
    url=os.getenv('DATABASE_URL','') if postgres else None
    if postgres and not url:pytest.skip('Disposable PostgreSQL required')
    conn=_history(monkeypatch,'crypto',postgres_url=url)
    request.addfinalizer(conn.db.close)
    conn.seed("UPDATE paper_regime_trade_metrics SET created_at=?,schema_version=? WHERE trade_id IN ('own-0','own-1')",
              ((START+timedelta(minutes=1) if kind=='late' else START-timedelta(minutes=1)).isoformat(),
               regime._SCHEMA_VERSION if kind=='originally_incomplete' else regime._ENTRY_READY_VERSION))
    captured=[];score=formula.score_entry
    def record(**kwargs):
        captured.append(kwargs)
        return score(**kwargs)
    monkeypatch.setattr(formula,'score_entry',record)
    before=[dict(r) for r in conn.db.execute('SELECT * FROM paper_regime_trade_metrics ORDER BY trade_id')]
    assert aeve.record_generation_outcomes()==1
    assert captured[0]['min_samples']==(2 if kind=='ready' else 0)
    assert captured[0]['loss_streak']==(2 if kind=='ready' else 0)
    assert aeve.record_generation_outcomes()==0
    assert [dict(r) for r in conn.db.execute('SELECT * FROM paper_regime_trade_metrics ORDER BY trade_id')]==before
