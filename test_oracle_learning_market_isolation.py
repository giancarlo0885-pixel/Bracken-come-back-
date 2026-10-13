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
            mfe_pct DOUBLE PRECISION,mae_pct DOUBLE PRECISION) ON COMMIT DROP""")
        conn.execute("""INSERT INTO oracle_brain_episodes VALUES
            ('crypto','exact','BTC-USD',1,1,-0.1),
            ('crypto','exact','ETH-USD',-2,0.2,-0.5)""")
        before_exit = advanced.exit_diagnostics(conn, market="crypto")
        before_concentration = advanced.portfolio_concentration(conn, market="crypto")
        conn.execute("""INSERT INTO oracle_brain_episodes VALUES
            ('cash','exact','BTC-USD',-900,20,-90),
            ('cash','exact','STOCK',1000,30,-100)""")
        assert advanced.exit_diagnostics(conn, market="crypto") == before_exit
        assert advanced.portfolio_concentration(conn, market="crypto") == before_concentration
        assert before_exit["samples"] == 2
        assert advanced.exit_diagnostics(conn, market="cash")["samples"] == 2
        assert advanced.exit_diagnostics(conn)["samples"] == 4
