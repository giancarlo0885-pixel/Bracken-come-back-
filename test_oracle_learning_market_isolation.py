import os
import sys
from types import SimpleNamespace

import pytest

import oracle_advanced_learning as advanced
import oracle_learning_validation as validation


class EmptyResult:
    def fetchall(self):
        return []

    def fetchone(self):
        return {}


class CaptureConnection:
    def __init__(self):
        self.queries = []

    def execute(self, query, params=()):
        self.queries.append((query, params))
        return EmptyResult()


@pytest.mark.parametrize("function,column", [
    (advanced.build_decision_replays, "d.market"),
    (advanced.refresh_setup_attribution, "market"),
    (advanced.classify_failures, "e.market"),
    (advanced.exit_diagnostics, "market"),
    (advanced.portfolio_concentration, "market"),
    (advanced.refresh_data_quality, "market"),
    (validation.sync_counterfactuals, "d.market"),
    (validation.refresh_calibration, "market"),
    (validation.refresh_weaknesses, "market"),
])
def test_learning_reader_scopes_market_before_limits_and_aggregates(function, column):
    conn = CaptureConnection()
    function(conn, market="crypto")
    sql, params = conn.queries[0]
    assert f"{column}=%s" in sql
    assert "crypto" in params
    assert sql.count("%s") == len(params)
    for boundary in ("ORDER BY", "GROUP BY", "LIMIT"):
        if boundary in sql:
            assert sql.index(f"{column}=%s") < sql.index(boundary)


@pytest.mark.parametrize("function", [advanced.sync_advanced_learning, validation.sync_learning_validation])
@pytest.mark.parametrize("market", ["cash", "crypto"])
def test_worker_pass_scopes_every_learning_read(function, market):
    conn = CaptureConnection()
    report = function(conn, market)
    assert report["market"] == market
    assert report["execution_impact"] == "NONE"
    reads = [(sql, params) for sql, params in conn.queries if sql.lstrip().startswith("SELECT")]
    assert reads
    assert all("market=%s" in sql and market in params for sql, params in reads)


@pytest.mark.parametrize("function", [advanced.sync_advanced_learning, validation.sync_learning_validation])
@pytest.mark.parametrize("market", [None, "unknown"])
def test_invalid_worker_market_fails_before_any_query(function, market):
    conn = CaptureConnection()
    with pytest.raises(ValueError):
        function(conn, market)
    assert not conn.queries


def test_runtime_scope_log_does_not_claim_isolation_for_degraded_pass(monkeypatch, caplog):
    import market_worker

    report = {"status": "ok", "validation": {"market": "crypto"},
              "advanced_learning": {"status": "degraded"}}
    monkeypatch.setitem(sys.modules, "oracle_brain_learning",
                        SimpleNamespace(sync_brain_learning=lambda market: report))
    with caplog.at_level("INFO"):
        market_worker._run_brain_learning_sync("crypto")
    assert "scoped=False" in caplog.text
    caplog.clear()
    report["advanced_learning"] = {"market": "crypto"}
    with caplog.at_level("INFO"):
        market_worker._run_brain_learning_sync("crypto")
    assert "scoped=True" in caplog.text


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="PostgreSQL CI service required")
def test_postgres_stock_outcomes_do_not_change_crypto_exit_or_concentration():
    from database import connect

    with connect() as conn:
        conn.execute("""CREATE TEMP TABLE oracle_brain_episodes (
            market TEXT,provenance_status TEXT,symbol TEXT,net_pnl DOUBLE PRECISION,
            mfe_pct DOUBLE PRECISION,mae_pct DOUBLE PRECISION,
            fees DOUBLE PRECISION DEFAULT 0,
            outcome_snapshot JSONB DEFAULT '{"accounting_version":"exact_round_trip_v1","cost_provenance":"exact_lot"}'::jsonb,
            entry_time TIMESTAMPTZ DEFAULT NOW()-INTERVAL '1 hour',
            exit_time TIMESTAMPTZ DEFAULT NOW()-INTERVAL '30 minutes'
            ) ON COMMIT DROP""")
        conn.execute("""INSERT INTO oracle_brain_episodes(market,provenance_status,symbol,net_pnl,mfe_pct,mae_pct) VALUES
            ('crypto','exact','BTC-USD',1,1,-0.1),
            ('crypto','exact','ETH-USD',-2,0.2,-0.5)""")
        before_exit = advanced.exit_diagnostics(conn, market="crypto")
        before_concentration = advanced.portfolio_concentration(conn, market="crypto")
        conn.execute("""INSERT INTO oracle_brain_episodes(market,provenance_status,symbol,net_pnl,mfe_pct,mae_pct) VALUES
            ('cash','exact','BTC-USD',-900,20,-90),
            ('cash','exact','STOCK',1000,30,-100)""")
        assert advanced.exit_diagnostics(conn, market="crypto") == before_exit
        assert advanced.portfolio_concentration(conn, market="crypto") == before_concentration
        assert before_exit["samples"] == 2
        assert advanced.exit_diagnostics(conn, market="cash")["samples"] == 2
        assert advanced.exit_diagnostics(conn)["samples"] == 4


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="PostgreSQL CI service required")
def test_postgres_legacy_or_incomplete_cost_outcomes_cannot_support_profitability_claims():
    from database import connect
    import json

    exact = {"accounting_version": "exact_round_trip_v1", "cost_provenance": "exact_lot"}
    with connect() as conn:
        conn.execute("""CREATE TEMP TABLE oracle_brain_episodes (
            market TEXT,provenance_status TEXT,symbol TEXT,net_pnl DOUBLE PRECISION,
            fees DOUBLE PRECISION,mfe_pct DOUBLE PRECISION,mae_pct DOUBLE PRECISION,
            outcome_snapshot JSONB,
            entry_time TIMESTAMPTZ DEFAULT NOW()-INTERVAL '1 hour',
            exit_time TIMESTAMPTZ DEFAULT NOW()-INTERVAL '30 minutes') ON COMMIT DROP""")
        rows = [
            ("exact", "ELIGIBLE", -2.0, 0.5, exact),
            ("exact", "LEGACY", 1000.0, 0.5, {}),
            ("exact", "WRONG_VERSION", 1000.0, 0.5,
             {"accounting_version": "exit_only", "cost_provenance": "exact_lot"}),
            ("exact", "WRONG_COST", 1000.0, 0.5,
             {"accounting_version": "exact_round_trip_v1", "cost_provenance": "fallback"}),
            ("partial", "PARTIAL", 1000.0, 0.5, exact),
            ("exact", "NEGATIVE_FEES", 1000.0, -1.0, exact),
            ("exact", "MISSING_FEES", 1000.0, None, exact),
            ("exact", "NONFINITE", float("inf"), 0.5, exact),
            ("exact", "NAN", float("nan"), 0.5, exact),
        ]
        for status, symbol, pnl, fees, outcome in rows:
            conn.execute("""INSERT INTO oracle_brain_episodes(market,provenance_status,symbol,net_pnl,fees,mfe_pct,mae_pct,outcome_snapshot) VALUES
                ('crypto',%s,%s,%s,%s,0.2,-0.5,%s::jsonb)""",
                         (status, symbol, pnl, fees, json.dumps(outcome)))
        metrics = advanced.exit_diagnostics(conn, market="crypto")
        assert metrics["samples"] == 1
        assert metrics["avg_giveback_mfe"] == 0.2
        assert advanced.portfolio_concentration(conn, market="crypto") == {
            "symbols": 1, "top3_concentration": 1.0,
        }
        assert conn.execute("SELECT COUNT(*) AS samples FROM oracle_brain_episodes").fetchone()["samples"] == len(rows)


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="PostgreSQL CI service required")
def test_postgres_brain_regime_display_uses_round_trip_costs():
    from database import connect
    import oracle_brain

    with connect() as conn:
        conn.execute("""CREATE TEMP TABLE paper_regime_trade_metrics (
            market TEXT,strategy TEXT,regime TEXT,net_pnl DOUBLE PRECISION,fees DOUBLE PRECISION,
            round_trip_net_pnl DOUBLE PRECISION,round_trip_fees DOUBLE PRECISION,cost_provenance TEXT,
            mfe_pct DOUBLE PRECISION,mae_pct DOUBLE PRECISION,excursion_sample_count INTEGER,
            entry_time TIMESTAMPTZ,exit_time TIMESTAMPTZ) ON COMMIT DROP""")
        conn.execute("""INSERT INTO paper_regime_trade_metrics VALUES
            ('crypto','test','range',0.3,0.3,-0.15,0.45,'exact_lot',NULL,NULL,0,NOW()-INTERVAL '1 hour',NOW()-INTERVAL '30 minutes'),
            ('crypto','test','range',900,0,NULL,NULL,'legacy_unknown',NULL,NULL,0,NOW()-INTERVAL '1 hour',NOW()-INTERVAL '30 minutes'),
            ('crypto','test','range',1000,0,1000,0,'exact_lot',NULL,NULL,0,NOW(),NOW()+INTERVAL '1 hour')""")

        def fetch(query, params=()):
            return list(conn.execute(query, params).fetchall()) if "FROM paper_regime_trade_metrics" in query else []

        cohort = oracle_brain.build_oracle_brain_snapshot(fetch, market_scope="crypto")["regime_economics"][0]
        assert cohort["samples"] == 1
        assert cohort["net_pnl"] == cohort["expectancy"] == -0.15
        assert cohort["fees"] == 0.45
        assert conn.execute("SELECT COUNT(*) AS total FROM paper_regime_trade_metrics").fetchone()["total"] == 3
