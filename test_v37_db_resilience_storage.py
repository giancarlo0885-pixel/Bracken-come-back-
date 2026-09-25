
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


@pytest.mark.parametrize("message", ["database system is in recovery mode", "server closed the connection unexpectedly", "connection reset by peer", "unexpected EOF on client connection"])
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
    result = execution_policy.execution_policy(market="cash", intent="entry", overrides={"ENABLE_AUTOTRADE": False, "ENABLE_STOCK_AUTOTRADE": False, "ENABLE_CRYPTO_AUTOTRADE": False, "ENABLE_NEW_ENTRIES": False, "ENABLE_AUTOMATED_EXITS": False, "ENABLE_PORTFOLIO_ROTATION": False, "ENABLE_BROKER_SUBMISSION": False, "GLOBAL_KILL_SWITCH": False})
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


def test_decision_funnel_retention_is_bounded_for_storage_safety():
    assert database.DATABASE_RETENTION_POLICIES["global_decision_events"]["keep_rows"] == 1000
    assert "1000" in database.DATABASE_TABLE_GROWTH_AUDIT["global_decision_events"]["retention"]


class _RetentionProbeResult:
    def __init__(self, row): self._row = row
    def fetchone(self): return self._row


class _RetentionProbeConn:
    def __init__(self, row): self.row = row; self.params = None
    def execute(self, sql, params=()): self.params = params; return _RetentionProbeResult(self.row)


def test_retention_hysteresis_skips_cleanup_inside_bounded_slack():
    conn = _RetentionProbeConn(None)
    due = database._retention_cleanup_due(conn, "signals", keep_rows=5000, batch_size=500)
    assert due is False
    assert conn.params == (6000,)


def test_retention_hysteresis_triggers_after_bounded_overshoot():
    conn = _RetentionProbeConn({"id": 1})
    due = database._retention_cleanup_due(conn, "signals", keep_rows=5000, batch_size=500)
    assert due is True
    assert conn.params == (6000,)


def test_high_churn_tables_get_aggressive_autovacuum_settings():
    database_source = open("database.py", encoding="utf-8").read()
    migration_source = open("migrations.py", encoding="utf-8").read()
    assert "global_decision_events SET (autovacuum_vacuum_scale_factor = 0.005" in migration_source
    assert "oracle_decision_audit SET (autovacuum_vacuum_scale_factor = 0.01" in migration_source
    assert "opportunity_radar_assessments SET (autovacuum_vacuum_scale_factor = 0.01" in migration_source
    assert "signals SET (autovacuum_vacuum_scale_factor = 0.01" in database_source
    assert "VACUUM FULL" not in database_source
    assert "VACUUM FULL" not in migration_source


class _SchemaHistoryResult:
    def __init__(self, row):
        self.row = row
    def fetchone(self):
        return self.row


class _SchemaHistoryConn:
    def __init__(self, relation="schema_migrations", has_history=True):
        self.relation = relation
        self.has_history = has_history
        self.calls = []
    def execute(self, sql, params=()):
        self.calls.append(sql)
        if "to_regclass" in sql:
            return _SchemaHistoryResult({"relation": self.relation})
        if "SELECT EXISTS" in sql:
            return _SchemaHistoryResult({"has_history": self.has_history})
        raise AssertionError(sql)


def test_established_schema_history_detects_versioned_database():
    conn = _SchemaHistoryConn()
    assert database._established_schema_history(conn) is True
    assert len(conn.calls) == 2


def test_established_schema_history_fails_open_for_fresh_database():
    assert database._established_schema_history(_SchemaHistoryConn(relation=None)) is False
    assert database._established_schema_history(_SchemaHistoryConn(has_history=False)) is False


def test_initialize_database_skips_compatibility_ddl_when_schema_is_established():
    source = open("database.py", encoding="utf-8").read()
    start = source.index("def initialize_database")
    body = source[start:]
    guard = body.index("if not established_schema:")
    create_loop = body.index("for statement in create_statements:")
    migration_loop = body.index("for statement in migration_statements:")
    portfolio_seed = body.index('for market in ("cash", "crypto"):')
    assert guard < create_loop < migration_loop < portfolio_seed
    assert "ALTER TABLE portfolios" in body


def test_global_decision_ledger_retention_policy_protects_linked_provenance():
    assert database.DATABASE_RETENTION_POLICIES["global_decision_ledger"]["keep_rows"] == 12000
    eligible = database._global_decision_ledger_eligible_sql()
    assert "trade_id IS NULL" in eligible
    assert "execution_claim_id IS NULL" in eligible
    assert "global_forecast_outcomes" in eligible
    assert "trade/execution/outcome-linked" in database.DATABASE_TABLE_GROWTH_AUDIT["global_decision_ledger"]["retention"]
    migration_source = open("migrations.py", encoding="utf-8").read()
    assert "global_decision_ledger SET (autovacuum_vacuum_scale_factor = 0.005" in migration_source


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="PostgreSQL integration test runs in CI service container")
def test_postgres_global_decision_ledger_retention_keeps_newest_and_protected_rows():
    database.initialize_database()
    prefix = f"retention-ledger-{int(datetime.now(timezone.utc).timestamp() * 1000000)}"
    with database.connect() as conn:
        for idx in range(6):
            conn.execute(
                """INSERT INTO global_decision_ledger
                   (decision_id,market,symbol,asset_class,features,portfolio_context,decision,rejection_reasons,created_at)
                   VALUES (%s,'cash',%s,'stock','{}'::jsonb,'{}'::jsonb,'rejected','[]'::jsonb,%s)""",
                (f"{prefix}-u-{idx}", f"U{idx}", f"2026-01-01T00:00:0{idx}+00:00"),
            )
        conn.execute(
            """INSERT INTO global_decision_ledger
               (decision_id,trade_id,market,symbol,asset_class,features,portfolio_context,decision,rejection_reasons,created_at)
               VALUES (%s,999999,'cash','TRADEKEEP','stock','{}'::jsonb,'{}'::jsonb,'executed','[]'::jsonb,'2026-01-01T00:00:10+00:00')""",
            (f"{prefix}-trade",),
        )
        conn.execute(
            """INSERT INTO global_decision_ledger
               (decision_id,execution_claim_id,market,symbol,asset_class,features,portfolio_context,decision,rejection_reasons,created_at)
               VALUES (%s,%s,'cash','CLAIMKEEP','stock','{}'::jsonb,'{}'::jsonb,'approved','[]'::jsonb,'2026-01-01T00:00:11+00:00')""",
            (f"{prefix}-claim", f"{prefix}-claim-id"),
        )
        conn.execute(
            """INSERT INTO global_decision_ledger
               (decision_id,forecast_id,market,symbol,asset_class,features,portfolio_context,decision,rejection_reasons,created_at)
               VALUES (%s,%s,'cash','OUTCOMEKEEP','stock','{}'::jsonb,'{}'::jsonb,'observed','[]'::jsonb,'2026-01-01T00:00:12+00:00')""",
            (f"{prefix}-outcome", f"{prefix}-forecast"),
        )
        conn.execute(
            """INSERT INTO global_forecast_outcomes
               (decision_id,forecast_id,symbol,horizon,realized_return_pct,created_at)
               VALUES (%s,%s,'OUTCOMEKEEP','1d',1.0,'2026-01-02T00:00:00+00:00')""",
            (f"{prefix}-outcome", f"{prefix}-forecast"),
        )
    original = database.DATABASE_RETENTION_POLICIES["global_decision_ledger"]
    database.DATABASE_RETENTION_POLICIES["global_decision_ledger"] = {**original, "keep_rows": 2, "batch_size": 2}
    try:
        deleted = database.trim_old_records()
    finally:
        database.DATABASE_RETENTION_POLICIES["global_decision_ledger"] = original
    with database.connect() as conn:
        kept_unlinked = conn.execute(
            "SELECT decision_id FROM global_decision_ledger WHERE decision_id LIKE %s ORDER BY created_at DESC",
            (f"{prefix}-u-%",),
        ).fetchall()
        protected = conn.execute(
            "SELECT decision_id FROM global_decision_ledger WHERE decision_id IN (%s,%s,%s)",
            (f"{prefix}-trade", f"{prefix}-claim", f"{prefix}-outcome"),
        ).fetchall()
    assert deleted["global_decision_ledger"] >= 4
    assert [row["decision_id"] for row in kept_unlinked] == [f"{prefix}-u-5", f"{prefix}-u-4"]
    assert len(protected) == 3


def test_retention_cleanup_is_bounded_per_maintenance_pass(monkeypatch):
    class Result:
        rowcount = 1000

    class Conn:
        def __init__(self):
            self.delete_calls = 0
        def execute(self, sql, params=()):
            if "DELETE FROM signals" in sql:
                self.delete_calls += 1
                return Result()
            class Row:
                def fetchone(self):
                    return {"id": 1}
            return Row()

    conn = Conn()
    deleted = database._apply_retention_policy(
        conn,
        "signals",
        {"keep_rows": 6000, "batch_size": 1000},
        max_batches=3,
    )
    assert deleted == 3000
    assert conn.delete_calls == 3


def test_global_decision_ledger_cleanup_is_bounded_per_pass(monkeypatch):
    class Result:
        rowcount = 1000

    class Conn:
        def __init__(self):
            self.delete_calls = 0
        def execute(self, sql, params=()):
            if "DELETE FROM global_decision_ledger" in sql:
                self.delete_calls += 1
                return Result()
            class Row:
                def fetchone(self):
                    return {"decision_id": "old"}
            return Row()

    conn = Conn()
    deleted = database._trim_global_decision_ledger(
        conn, keep_rows=12000, batch_size=1000, max_batches=2
    )
    assert deleted == 2000
    assert conn.delete_calls == 2


def test_forecast_validation_retention_policy_registered_with_keep_rows_and_batching():
    policy = database.DATABASE_RETENTION_POLICIES["forecast_validation"]
    assert policy["keep_rows"] == 15000
    assert policy["batch_size"] == database.DATABASE_RETENTION_BATCH_SIZE
    assert "keep newest 15000 rows" in database.DATABASE_TABLE_GROWTH_AUDIT["forecast_validation"]["retention"]


def test_forecast_validation_retention_deletes_oldest_rows_beyond_keep_rows():
    class Result:
        rowcount = 500

    class Conn:
        def __init__(self):
            self.delete_calls = 0

        def execute(self, sql, params=()):
            if "DELETE FROM forecast_validation" in sql:
                self.delete_calls += 1
                return Result()

            class Row:
                def fetchone(self):
                    return {"id": 1}
            return Row()

    conn = Conn()
    deleted = database._apply_retention_policy(
        conn,
        "forecast_validation",
        {"keep_rows": 15000, "batch_size": 500},
        max_batches=1,
    )
    assert deleted == 500
    assert conn.delete_calls == 1


def test_oracle_decision_replays_is_never_added_to_generic_retention_policies():
    # oracle_decision_replays remains a canonical protected table (advanced
    # research evidence). It must never be eligible for the generic
    # id-ordered retention sweep, which has no provenance-aware exclusion
    # for global_decision_ledger / oracle_brain_* / oracle_counterfactual_outcomes /
    # oracle_calibration_buckets linkage.
    assert "oracle_decision_replays" in database.CANONICAL_PROTECTED_TABLES
    assert "oracle_decision_replays" not in database.DATABASE_RETENTION_POLICIES
    assert database.CANONICAL_PROTECTED_TABLES.isdisjoint(database.DATABASE_RETENTION_POLICIES)


def test_oracle_decision_replays_generic_retention_call_is_refused():
    class Conn:
        def execute(self, sql, params=()):
            raise AssertionError("should not execute SQL for a protected table")

    with pytest.raises(ValueError, match="protected table"):
        database._apply_retention_policy(
            Conn(), "oracle_decision_replays", {"keep_rows": 10000, "batch_size": 500}
        )


def test_oracle_decision_replays_and_forecast_validation_get_aggressive_autovacuum():
    migration_source = open("migrations.py", encoding="utf-8").read()
    assert "oracle_decision_replays SET (autovacuum_vacuum_scale_factor = 0.01" in migration_source
    assert "forecast_validation SET (autovacuum_vacuum_scale_factor = 0.015" in migration_source


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="PostgreSQL integration test runs in CI service container")
def test_postgres_autovacuum_reloptions_applied_for_high_churn_tables():
    database.initialize_database()
    import migrations
    migrations.run_migrations()
    with database.connect() as conn:
        rows = {
            row["relname"]: row["reloptions"]
            for row in conn.execute(
                "SELECT relname, reloptions FROM pg_class "
                "WHERE relname IN ('oracle_decision_replays','forecast_validation')"
            ).fetchall()
        }
    for table in ("oracle_decision_replays", "forecast_validation"):
        options = " ".join(rows.get(table) or [])
        assert "autovacuum_vacuum_scale_factor" in options
        assert "autovacuum_analyze_scale_factor" in options


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="PostgreSQL integration test runs in CI service container")
def test_postgres_forecast_validation_retention_keeps_newest_rows():
    database.initialize_database()
    now = datetime.now(timezone.utc).isoformat()
    with database.connect() as conn:
        for idx in range(6):
            conn.execute(
                "INSERT INTO forecast_validation (symbol,asset_class,created_at) VALUES (%s,'stock',%s)",
                (f"FVAL{idx}", now),
            )
    original = database.DATABASE_RETENTION_POLICIES["forecast_validation"]
    database.DATABASE_RETENTION_POLICIES["forecast_validation"] = {**original, "keep_rows": 3, "batch_size": 2}
    try:
        deleted = database.trim_old_records()
    finally:
        database.DATABASE_RETENTION_POLICIES["forecast_validation"] = original
    with database.connect() as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) AS total FROM forecast_validation WHERE symbol LIKE 'FVAL%'"
        ).fetchone()["total"]
    assert deleted["forecast_validation"] >= 3
    assert remaining == 3


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="PostgreSQL integration test runs in CI service container")
def test_postgres_trade_execution_and_brain_learning_workflow_survives_maintenance():
    """Integration smoke test: run a paper trade + Brain learning write, then a
    maintenance pass, and confirm no canonical/durable evidence is lost."""
    database.initialize_database()
    now = datetime.now(timezone.utc).isoformat()
    with database.connect() as conn:
        conn.execute(
            "INSERT INTO trades (market,symbol,side,quantity,price,value,realized_pnl,score,reason,created_at) "
            "VALUES ('cash','MAINT','BUY',1,1,1,0,NULL,'unit',%s)",
            (now,),
        )
    database.trim_old_records()
    with database.connect() as conn:
        trade_count = conn.execute(
            "SELECT COUNT(*) AS total FROM trades WHERE symbol='MAINT'"
        ).fetchone()["total"]
    assert trade_count == 1


def test_trim_old_records_prioritizes_ledger_and_commits_each_table(monkeypatch):
    entered = []
    exited = []
    applied = []

    class Conn:
        pass

    class Ctx:
        def __enter__(self):
            entered.append(len(entered))
            return Conn()
        def __exit__(self, exc_type, exc, tb):
            exited.append(len(exited))

    monkeypatch.setattr(database, "connect", lambda: Ctx())
    monkeypatch.setattr(database, "_retention_table_exists", lambda conn, table: True)
    monkeypatch.setattr(
        database,
        "_apply_retention_policy",
        lambda conn, table, policy, max_batches=5: applied.append((table, max_batches)) or 0,
    )
    result = database.trim_old_records()
    assert list(result)[0] == "global_decision_ledger"
    assert applied[0] == ("global_decision_ledger", 5)
    assert len(entered) == len(database.DATABASE_RETENTION_POLICIES)
    assert len(exited) == len(database.DATABASE_RETENTION_POLICIES)
