
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import os
import pytest

import config
import database
import execution_policy
import market_worker


def test_database_ready_missing_url_is_configuration_failure(monkeypatch):
    monkeypatch.setattr(database, "DATABASE_URL", "")
    result = database.database_ready()
    assert result["ok"] is False
    assert result["configuration_error"] is True
    assert result["transient"] is False


@pytest.mark.parametrize(
    "message",
    [
        "database system is in recovery mode",
        "server closed the connection unexpectedly",
        "connection reset by peer",
        "unexpected EOF on client connection",
    ],
)
def test_transient_database_errors_are_classified(message):
    assert database.is_transient_database_error(RuntimeError(message)) is True


def test_wait_for_database_ready_retries_until_available(monkeypatch):
    attempts = []

    def fake_ready():
        attempts.append(1)
        if len(attempts) < 3:
            return {"ok": False, "transient": True, "message": "database system is in recovery mode"}
        return {"ok": True, "transient": False, "message": "ready"}

    monkeypatch.setattr(database, "database_ready", fake_ready)
    result = database.wait_for_database_ready(initial_delay=0.01, max_delay=0.01)
    assert result["ok"] is True
    assert len(attempts) == 3


def test_wait_for_database_ready_stops_cleanly(monkeypatch):
    event = SimpleNamespace(is_set=lambda: True, wait=lambda seconds: True)
    monkeypatch.setattr(database, "database_ready", lambda: {"ok": False, "transient": True, "message": "restarting"})
    result = database.wait_for_database_ready(stop_event=event, initial_delay=0.01, max_delay=0.01)
    assert result["stopped"] is True


def test_wait_for_database_ready_missing_url_fails_loudly(monkeypatch):
    monkeypatch.setattr(database, "database_ready", lambda: {"ok": False, "transient": False, "message": "DATABASE_URL is missing"})
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        database.wait_for_database_ready(initial_delay=0.01, max_delay=0.01)


def test_worker_bootstrap_waits_then_runs_migrations(monkeypatch):
    calls = []
    monkeypatch.setattr(market_worker, "_wait_for_worker_database", lambda label: calls.append("wait") or True)
    monkeypatch.setattr(market_worker, "_ensure_status_table", lambda: calls.append("status"))
    monkeypatch.setattr(market_worker, "bootstrap_database_with_lock", lambda fn: calls.append("bootstrap") or fn())
    import migrations

    monkeypatch.setattr(migrations, "run_migrations", lambda: calls.append("migrations"))
    assert market_worker._bootstrap_worker_database("Stock Market") is True
    assert calls == ["wait", "bootstrap", "migrations", "status"]


def test_worker_bootstrap_retries_transient_initialization(monkeypatch):
    calls = {"bootstrap": 0}
    monkeypatch.setattr(market_worker, "_wait_for_worker_database", lambda label: True)
    monkeypatch.setattr(market_worker, "_ensure_status_table", lambda: None)
    monkeypatch.setattr(market_worker, "is_transient_database_error", lambda exc: "recovery" in str(exc))

    def fake_bootstrap(fn):
        calls["bootstrap"] += 1
        if calls["bootstrap"] == 1:
            raise RuntimeError("database system is in recovery mode")
        return None

    monkeypatch.setattr(market_worker, "bootstrap_database_with_lock", fake_bootstrap)
    assert market_worker._bootstrap_worker_database("Stock Market") is True
    assert calls["bootstrap"] == 2


def test_worker_bootstrap_programming_error_fails_loudly(monkeypatch):
    monkeypatch.setattr(market_worker, "_wait_for_worker_database", lambda label: True)
    monkeypatch.setattr(market_worker, "bootstrap_database_with_lock", lambda fn: (_ for _ in ()).throw(ValueError("bad SQL")))
    monkeypatch.setattr(market_worker, "is_transient_database_error", lambda exc: False)
    with pytest.raises(ValueError, match="bad SQL"):
        market_worker._bootstrap_worker_database("Stock Market")


def test_central_execution_policy_remains_disabled():
    result = execution_policy.execution_policy(
        market="cash",
        intent="entry",
        overrides={
            "ENABLE_AUTOTRADE": False,
            "ENABLE_STOCK_AUTOTRADE": False,
            "ENABLE_CRYPTO_AUTOTRADE": False,
            "ENABLE_NEW_ENTRIES": False,
            "ENABLE_AUTOMATED_EXITS": False,
            "ENABLE_PORTFOLIO_ROTATION": False,
            "ENABLE_BROKER_SUBMISSION": False,
            "GLOBAL_KILL_SWITCH": False,
        },
    )
    assert result.allowed is False


def test_retention_policies_do_not_include_canonical_tables():
    assert database.CANONICAL_PROTECTED_TABLES.isdisjoint(database.DATABASE_RETENTION_POLICIES)
    assert {"signals", "forecasts", "equity_snapshots", "alerts", "intelligence_events", "opportunity_rankings", "oracle_decision_audit", "opportunity_radar_assessments", "global_decision_events"} <= set(database.DATABASE_RETENTION_POLICIES)
    assert database.DATABASE_TABLE_GROWTH_AUDIT["global_asset_identities"]["retention"] == "never auto-delete"
    assert database.DATABASE_TABLE_GROWTH_AUDIT["global_model_governance"]["retention"] == "never auto-delete"
    assert "keep newest" in database.DATABASE_TABLE_GROWTH_AUDIT["global_decision_events"]["retention"]


def test_storage_report_capacity_status(monkeypatch):
    monkeypatch.setattr(database, "DATABASE_VOLUME_CAPACITY_GB", 1.0)

    class FakeConn:
        def execute(self, sql, params=()):
            if "pg_database_size" in sql:
                return SimpleNamespace(fetchone=lambda: {"name": "unit", "bytes": int(0.8 * 1024**3)})
            return SimpleNamespace(fetchall=lambda: [{"table": "signals", "total_bytes": 2048, "table_bytes": 1024, "index_bytes": 1024, "live_rows": 10, "dead_rows": 1, "last_autovacuum": None, "last_autoanalyze": None}])

    class Ctx:
        def __enter__(self): return FakeConn()
        def __exit__(self, *args): return False

    monkeypatch.setattr(database, "connect", lambda: Ctx())
    report = database.database_storage_report()
    assert report["status"] == "warning"
    assert report["largest_tables"][0]["table"] == "signals"
    assert report["largest_tables"][0]["index_bytes"] == 1024
    assert report["archive_candidates"][0]["table"] == "signals"
    assert report["capacity_projection"]["projected_30d_bytes"] == int(int(0.8 * 1024**3) * 1.10)


def test_maintenance_lock_skip_does_not_crash(monkeypatch):
    class LockCtx:
        def __enter__(self): return False
        def __exit__(self, *args): return False

    monkeypatch.setattr(database, "database_advisory_lock", lambda *args, **kwargs: LockCtx())
    result = database.run_database_maintenance()
    assert result["ok"] is True
    assert result["skipped"] is True


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="PostgreSQL integration test runs in CI service container")
def test_postgres_storage_report_and_retention_protects_canonical_tables():
    database.initialize_database()
    now = datetime.now(timezone.utc).isoformat()
    with database.connect() as conn:
        for idx in range(8):
            conn.execute("INSERT INTO signals (market,symbol,price,score,action,confidence,details,created_at) VALUES ('cash',%s,1,1,'HOLD',0.1,'{}',%s)", (f"RET{idx}", now))
        conn.execute("INSERT INTO trades (market,symbol,side,quantity,price,value,realized_pnl,score,reason,created_at) VALUES ('cash','KEEP','BUY',1,1,1,0,NULL,'unit',%s)", (now,))
    original = database.DATABASE_RETENTION_POLICIES["signals"]
    database.DATABASE_RETENTION_POLICIES["signals"] = {**original, "keep_rows": 3, "batch_size": 2}
    try:
        deleted = database.trim_old_records()
    finally:
        database.DATABASE_RETENTION_POLICIES["signals"] = original
    with database.connect() as conn:
        signal_count = conn.execute("SELECT COUNT(*) AS total FROM signals WHERE symbol LIKE 'RET%'").fetchone()["total"]
        trade_count = conn.execute("SELECT COUNT(*) AS total FROM trades WHERE symbol='KEEP'").fetchone()["total"]
    report = database.database_storage_report()
    assert deleted["signals"] >= 5
    assert signal_count == 3
    assert trade_count >= 1
    assert report["database_bytes"] > 0
    assert report["largest_tables"]


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="PostgreSQL integration test runs in CI service container")
def test_postgres_maintenance_advisory_lock_allows_one_runner():
    database.initialize_database()
    with database.database_advisory_lock(database.DATABASE_MAINTENANCE_LOCK_NAME, wait=False) as locked:
        assert locked is True
        result = database.run_database_maintenance()
    assert result["skipped"] is True


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="PostgreSQL integration test runs in CI service container")
def test_postgres_bootstrap_advisory_lock_runs_migrations_once():
    called = []
    database.bootstrap_database_with_lock(lambda: called.append("migrate"))
    assert called == ["migrate"]
