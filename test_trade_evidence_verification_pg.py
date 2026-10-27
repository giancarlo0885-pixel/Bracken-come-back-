"""Committed-trade verification against independent PostgreSQL snapshots.

All writes are confined to a disposable UUID schema on a local test database.
The verifier itself receives a separate read-only connection, never the writer.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
import uuid

import pytest


@pytest.fixture
def committed_evidence_db():
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict
    from psycopg.rows import dict_row

    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        pytest.skip("PostgreSQL integration only")
    settings = conninfo_to_dict(dsn)
    database_name = settings.get("dbname", "")
    if (settings.get("host", "") not in {"localhost", "127.0.0.1", "::1"}
            or not (database_name.endswith("_test") or database_name.startswith("test_"))):
        pytest.skip("Disposable-schema tests require a local, explicitly named test database")

    schema = "trade_evidence_pg_" + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    options = "-c search_path=" + schema + ",pg_catalog"
    try:
        with psycopg.connect(dsn, options=options, row_factory=dict_row) as setup:
            setup.execute("""CREATE TABLE trade_ledger (
                trade_id text PRIMARY KEY,market text,symbol text,side text,
                quantity float8,entry_price float8,exit_price float8,
                gross_pnl float8,fees float8,net_pnl float8,
                entry_time text,exit_time text,broker_mode text,account_environment text,
                entry_signal_id text,entry_decision_id text,entry_forecast_id text,
                entry_quote_id text,decision_timestamp text,quote_timestamp text,
                feature_snapshot jsonb,risk_snapshot jsonb,status text,id bigserial)""")
            setup.execute("""CREATE TABLE paper_regime_trade_metrics (
                trade_id text PRIMARY KEY,market text,symbol text,strategy text,regime text,
                entry_price float8,exit_price float8,entry_time timestamptz,exit_time timestamptz,
                net_pnl float8,fees float8,round_trip_net_pnl float8,round_trip_fees float8,
                cost_provenance text,mfe_pct float8,mae_pct float8,excursion_sample_count int)""")
            setup.execute("""CREATE TABLE oracle_brain_episodes (
                episode_key text PRIMARY KEY,trade_id text UNIQUE,market text,symbol text,
                strategy text,regime text,entry_time timestamptz,exit_time timestamptz,
                net_pnl float8,fees float8,return_pct float8,provenance_status text,
                feature_snapshot jsonb,outcome_snapshot jsonb,execution_impact text)""")
            setup.execute("""CREATE TABLE paper_aeve_generation_outcomes (
                id bigserial PRIMARY KEY,generation int,trade_id text,net_pnl float8,
                observed_at timestamptz,entry_time timestamptz,cost_provenance text,
                entry_evidence_complete boolean,provenance_version int,config_hash text,
                config_json jsonb,would_trade boolean,score float8)""")
        yield dsn, options
    finally:
        with psycopg.connect(dsn, autocommit=True) as cleanup:
            cleanup.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def _write_exact_close(conn, trade_id, *, brain=True, aeve=True):
    """The exit looks profitable; complete entry plus exit costs make it a loss."""
    entry = datetime(2026, 1, 1, tzinfo=timezone.utc)
    exit_time = entry + timedelta(hours=1)
    features = {"entry_pattern": "dip_rebound", "regime": "range"}
    conn.execute("""INSERT INTO trade_ledger (
        trade_id,market,symbol,side,quantity,entry_price,exit_price,gross_pnl,fees,net_pnl,
        entry_time,exit_time,broker_mode,account_environment,entry_signal_id,entry_decision_id,
        entry_forecast_id,entry_quote_id,decision_timestamp,quote_timestamp,
        feature_snapshot,risk_snapshot,status) VALUES (
        %s,'crypto','TEST-USD','SELL',1,100,100.3,0.3,0.1,0.2,
        %s,%s,'PAPER','PAPER','signal-1','decision-1','forecast-1','quote-1',
        %s,%s,%s::jsonb,'{}'::jsonb,'CLOSED')""",
        (trade_id, entry.isoformat(), exit_time.isoformat(), entry.isoformat(),
         entry.isoformat(), json.dumps(features)))
    conn.execute("""INSERT INTO paper_regime_trade_metrics VALUES (
        %s,'crypto','TEST-USD','oracle_council_v3','range',100,100.3,%s,%s,
        0.2,0.1,-0.2,0.5,'exact_lot',NULL,NULL,0)""", (trade_id, entry, exit_time))
    if brain:
        outcome = {"accounting_version": "exact_round_trip_v1", "cost_provenance": "exact_lot",
                   "outcome": "negative", "net_pnl": -0.2, "fees": 0.5,
                   "gross_pnl": 0.3, "entry_signal_id": "signal-1"}
        conn.execute("""INSERT INTO oracle_brain_episodes VALUES (
            %s,%s,'crypto','TEST-USD','oracle_council_v3','range',%s,%s,
            -0.2,0.5,-0.2,'exact',%s::jsonb,%s::jsonb,'NONE')""",
            ("trade:" + trade_id, trade_id, entry, exit_time, json.dumps(features), json.dumps(outcome)))
    if aeve:
        conn.execute("""INSERT INTO paper_aeve_generation_outcomes (
            generation,trade_id,net_pnl,observed_at,entry_time,cost_provenance,
            entry_evidence_complete,provenance_version,config_hash,config_json,would_trade,score
            ) VALUES (3,%s,-0.2,%s,%s,'exact_lot',TRUE,7,'fixture-config','{}',FALSE,0)""",
            (trade_id, exit_time, entry))


def _reader_factory(dsn, options, observations, *, reject_write=False, after_snapshot=None):
    import psycopg
    from psycopg.rows import dict_row

    class ReadOnlyProbe:
        def __init__(self, connection):
            self.connection = connection
            self.write_checked = False
            self.snapshot_checked = False

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def execute(self, query, params=None, **kwargs):
            result = self.connection.execute(query, params, **kwargs)
            if str(query).lstrip().upper().startswith("SELECT"):
                read_only = self.connection.execute("SHOW transaction_read_only").fetchone()
                isolation = self.connection.execute("SHOW transaction_isolation").fetchone()
                observations.append((self.connection.info.backend_pid,
                                     read_only["transaction_read_only"],
                                     isolation["transaction_isolation"]))
                if after_snapshot and not self.snapshot_checked:
                    self.snapshot_checked = True
                    after_snapshot()
                if reject_write and not self.write_checked:
                    self.write_checked = True
                    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                        with self.connection.transaction():
                            self.connection.execute("INSERT INTO trade_ledger(trade_id) VALUES ('forbidden-reader-write')")
            return result

    @contextmanager
    def factory():
        with psycopg.connect(dsn, options=options, row_factory=dict_row) as reader:
            yield ReadOnlyProbe(reader)

    return factory


def test_postgres_uncommitted_and_rolled_back_evidence_is_invisible(committed_evidence_db):
    import psycopg
    from psycopg.rows import dict_row
    from trade_evidence_verification import verify_committed_trade_evidence

    dsn, options = committed_evidence_db
    observations = []
    factory = _reader_factory(dsn, options, observations)
    with psycopg.connect(dsn, options=options, row_factory=dict_row) as writer:
        writer_pid = writer.info.backend_pid
        for trade_id in ("commit-proof-a", "commit-proof-b"):
            _write_exact_close(writer, trade_id)
        before = verify_committed_trade_evidence(["commit-proof-a", "commit-proof-b"], connect_factory=factory)
        assert before["status"] == "ok"
        assert before["transaction_read_only"] is True
        assert all(row["ledger_status"] == "MISSING" for row in before["records"])
        assert all(row["brain_status"] != "MATERIALIZED_EXACT" for row in before["records"])
        assert all(row["aeve_committed_count"] == 0 for row in before["records"])
        writer.commit()
        after = verify_committed_trade_evidence(["commit-proof-a", "commit-proof-b"], connect_factory=factory)
        assert {row["trade_id"] for row in after["records"]} == {"commit-proof-a", "commit-proof-b"}
        assert all(row["ledger_status"] == "COMMITTED" for row in after["records"])
        assert all(row["brain_status"] == "MATERIALIZED_EXACT" for row in after["records"])
        assert all(row["aeve_committed_count"] == 1 for row in after["records"])
        _write_exact_close(writer, "rollback-proof")
        writer.rollback()
        rolled_back = verify_committed_trade_evidence(["rollback-proof"], connect_factory=factory)
        assert rolled_back["records"][0]["ledger_status"] == "MISSING"
        assert rolled_back["records"][0]["brain_status"] != "MATERIALIZED_EXACT"
        assert rolled_back["records"][0]["aeve_committed_count"] == 0
    assert observations
    assert all(pid != writer_pid and read_only == "on" and isolation == "repeatable read"
               for pid, read_only, isolation in observations)


def test_postgres_verification_is_read_only_and_distinguishes_pending_brain(committed_evidence_db):
    import psycopg
    from psycopg.rows import dict_row
    from trade_evidence_verification import verify_committed_trade_evidence

    dsn, options = committed_evidence_db
    observations = []
    with psycopg.connect(dsn, options=options, row_factory=dict_row) as writer:
        _write_exact_close(writer, "pending-brain", brain=False, aeve=False)
        writer.commit()
    report = verify_committed_trade_evidence(["pending-brain"], connect_factory=
        _reader_factory(dsn, options, observations, reject_write=True))
    assert report["status"] == "ok"
    assert report["transaction_read_only"] is True
    assert report["records"][0]["ledger_status"] == "COMMITTED"
    assert report["records"][0]["brain_status"] == "PENDING"
    with psycopg.connect(dsn, options=options, row_factory=dict_row) as audit:
        assert audit.execute("SELECT COUNT(*) AS n FROM trade_ledger").fetchone()["n"] == 1
        assert audit.execute("SELECT COUNT(*) AS n FROM oracle_brain_episodes").fetchone()["n"] == 0
        assert audit.execute("SELECT COUNT(*) AS n FROM paper_aeve_generation_outcomes").fetchone()["n"] == 0
    assert observations
    assert all(read_only == "on" and isolation == "repeatable read"
               for _, read_only, isolation in observations)


def test_postgres_exit_only_or_mismatched_brain_is_not_exact_materialization(committed_evidence_db):
    import psycopg
    from psycopg.rows import dict_row
    from trade_evidence_verification import verify_committed_trade_evidence

    dsn, options = committed_evidence_db
    with psycopg.connect(dsn, options=options, row_factory=dict_row) as writer:
        _write_exact_close(writer, "exit-only-brain")
        _write_exact_close(writer, "mismatched-brain")
        writer.execute("UPDATE oracle_brain_episodes SET net_pnl=0.2,fees=0.1,outcome_snapshot='{}' WHERE trade_id='exit-only-brain'")
        writer.execute("UPDATE oracle_brain_episodes SET net_pnl=0.2 WHERE trade_id='mismatched-brain'")
        writer.commit()
    report = verify_committed_trade_evidence(["exit-only-brain", "mismatched-brain"], connect_factory=
        _reader_factory(dsn, options, []))
    records = {row["trade_id"]: row for row in report["records"]}
    assert records["exit-only-brain"]["brain_status"] == "UNQUALIFIED"
    assert records["mismatched-brain"]["brain_status"] == "MISMATCH"
    assert all(row["ledger_status"] == "COMMITTED" for row in records.values())


def test_postgres_default_selection_is_bounded_and_includes_orphan_evaluations(committed_evidence_db):
    import psycopg
    from psycopg.rows import dict_row
    from trade_evidence_verification import verify_committed_trade_evidence

    dsn, options = committed_evidence_db
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with psycopg.connect(dsn, options=options, row_factory=dict_row) as writer:
        for trade_id in ("old-close", "recent-close-a", "recent-close-b"):
            _write_exact_close(writer, trade_id, aeve=False)
        for index in range(15):
            writer.execute("""INSERT INTO paper_aeve_generation_outcomes (
                generation,trade_id,net_pnl,observed_at) VALUES (3,%s,-0.2,%s)""",
                (f"orphan-evaluation-{index:02d}", start + timedelta(minutes=index)))
        writer.commit()
    report = verify_committed_trade_evidence(connect_factory=_reader_factory(dsn, options, []))
    records = {row["trade_id"]: row for row in report["records"]}
    assert len(records) == 12
    assert "old-close" not in records
    assert records["recent-close-a"]["brain_status"] == "MATERIALIZED_EXACT"
    assert records["recent-close-b"]["brain_status"] == "MATERIALIZED_EXACT"
    for index in range(5, 15):
        row = records[f"orphan-evaluation-{index:02d}"]
        assert row["ledger_status"] == "MISSING"
        assert row["aeve_committed_count"] == 1
        assert row["brain_status"] != "MATERIALIZED_EXACT"


def test_postgres_recent_aeve_sample_preserves_latest_distinct_observed_order(committed_evidence_db):
    import psycopg
    from psycopg.rows import dict_row
    from trade_evidence_verification import verify_committed_trade_evidence

    dsn, options = committed_evidence_db
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with psycopg.connect(dsn, options=options, row_factory=dict_row) as writer:
        with writer.cursor() as cursor:
            cursor.executemany("""INSERT INTO paper_aeve_generation_outcomes (
                generation,trade_id,net_pnl,observed_at) VALUES (3,%s,-0.2,%s)""",
                [(f"recent-orphan-{index:04d}", start + timedelta(minutes=index))
                 for index in range(540)])
        # A late insert with an old observation must not displace a newer
        # observation when the bounded candidate window is ranked.
        writer.execute("""INSERT INTO paper_aeve_generation_outcomes (
            generation,trade_id,net_pnl,observed_at) VALUES (3,%s,-0.2,%s)""",
            ("late-backfill", start - timedelta(days=1)))
        writer.commit()

    report = verify_committed_trade_evidence(connect_factory=_reader_factory(dsn, options, []))
    expected = {f"recent-orphan-{index:04d}" for index in range(530, 540)}
    records = {row["trade_id"]: row for row in report["records"]}
    assert set(records) == expected
    assert all(row["ledger_status"] == "MISSING" for row in records.values())
    assert all(row["aeve_committed_count"] == 1 for row in records.values())
    assert report["transaction_read_only"] is True


def test_postgres_missing_optional_learning_tables_are_unavailable_not_missing(committed_evidence_db):
    import psycopg
    from psycopg.rows import dict_row
    from trade_evidence_verification import verify_committed_trade_evidence

    dsn, options = committed_evidence_db
    with psycopg.connect(dsn, options=options, row_factory=dict_row) as writer:
        _write_exact_close(writer, "optional-tables-hidden")
        # Rename only this disposable fixture's tables; preserve their rows.
        writer.execute("ALTER TABLE oracle_brain_episodes RENAME TO held_brain_episodes")
        writer.execute("ALTER TABLE paper_regime_trade_metrics RENAME TO held_regime_metrics")
        writer.execute("ALTER TABLE paper_aeve_generation_outcomes RENAME TO held_aeve_outcomes")
        writer.commit()
    report = verify_committed_trade_evidence(["optional-tables-hidden"], connect_factory=
        _reader_factory(dsn, options, []))
    record = report["records"][0]
    assert record["ledger_status"] == "COMMITTED"
    assert record["brain_status"] == "UNAVAILABLE"
    assert record["aeve_status"] == "UNAVAILABLE"


def test_postgres_one_receipt_uses_one_snapshot_during_concurrent_commit(committed_evidence_db):
    import psycopg
    from psycopg.rows import dict_row
    from trade_evidence_verification import verify_committed_trade_evidence

    dsn, options = committed_evidence_db
    with psycopg.connect(dsn, options=options, row_factory=dict_row) as writer:
        _write_exact_close(writer, "snapshot-proof")
        writer.commit()
        writer.execute("UPDATE oracle_brain_episodes SET net_pnl=0.2 WHERE trade_id='snapshot-proof'")
        # Commit the discrepancy after the verifier acquires its snapshot but
        # before it queries the ledger, metrics, and Brain records.
        first = verify_committed_trade_evidence(["snapshot-proof"], connect_factory=
            _reader_factory(dsn, options, [], after_snapshot=writer.commit))
        assert first["records"][0]["brain_status"] == "MATERIALIZED_EXACT"
        second = verify_committed_trade_evidence(["snapshot-proof"], connect_factory=
            _reader_factory(dsn, options, []))
        assert second["records"][0]["brain_status"] == "MISMATCH"
        assert first["snapshot"] != second["snapshot"]


def _write_source_row(conn, trade_id, *, market="cash", side="SELL", broker="PAPER", account="PAPER"):
    conn.execute("""INSERT INTO trade_ledger
        (trade_id,market,side,broker_mode,account_environment,entry_time,exit_time)
        VALUES (%s,%s,%s,%s,%s,'2026-01-01T00:00:00+00:00','2026-01-01T01:00:00+00:00')""",
        (trade_id, market, side, broker, account))


def test_postgres_cash_no_recent_trades_does_not_claim_empty_historical_ledger(committed_evidence_db):
    import psycopg
    from psycopg.rows import dict_row
    from trade_evidence_verification import verify_committed_trade_evidence

    dsn, options = committed_evidence_db
    private_tag = "private-account-secret-must-not-leave-database"
    with psycopg.connect(dsn, options=options, row_factory=dict_row) as writer:
        _write_source_row(writer, "stock-history", market="stock")
        _write_source_row(writer, "uppercase-history", market="CASH")
        _write_source_row(writer, "crypto-history", market="crypto")
        _write_source_row(writer, "broker-tag-history", broker="paper")
        _write_source_row(writer, "account-tag-history", account="paper")
        _write_source_row(writer, "side-tag-history", side="sell")
        _write_source_row(writer, "private-tag-history", market=private_tag)
        writer.commit()
    observations = []
    report = verify_committed_trade_evidence(market="cash", connect_factory=
        _reader_factory(dsn, options, observations, reject_write=True))
    assert report["records"] == []
    diagnostic = report["source_diagnostics"]
    totals = diagnostic["totals"]
    assert totals == {"ledger_rows": 7, "canonical_cash_rows": 3,
        "noncanonical_stock_label_rows": 2, "excluded_noncanonical_market_rows": 4,
        "excluded_nonpaper_broker_rows": 1, "excluded_nonpaper_account_rows": 1,
        "excluded_unsupported_side_rows": 1, "source_buy_rows": 0, "source_sell_rows": 0}
    assert diagnostic["reason"] == "NO_CANONICAL_CASH_PAPER_BUY_SELL_ROWS"
    labels = {(row["market"], row["side"], row["broker_mode"], row["account_environment"])
              for row in diagnostic["ledger_inventory"]}
    assert ("stock", "SELL", "PAPER", "PAPER") in labels
    assert ("CASH", "SELL", "PAPER", "PAPER") in labels
    assert ("cash", "SELL", "paper", "PAPER") in labels
    assert ("cash", "SELL", "PAPER", "paper") in labels
    assert ("OTHER", "SELL", "PAPER", "PAPER") in labels
    assert private_tag not in json.dumps(report)
    assert diagnostic["qualification_status"] == "NOT_EVALUATED_BY_INVENTORY"
    assert diagnostic["bounded_exact_linked_sell_records"] == 0
    assert diagnostic["historical_link_inference"] == "NONE"
    assert observations and all(read_only == "on" and isolation == "repeatable read"
                                for _, read_only, isolation in observations)


def test_postgres_cash_unlinked_history_remains_visible_and_unqualified_without_repairs(committed_evidence_db):
    import psycopg
    from psycopg.rows import dict_row
    from trade_evidence_verification import verify_committed_trade_evidence

    dsn, options = committed_evidence_db
    with psycopg.connect(dsn, options=options, row_factory=dict_row) as writer:
        writer.execute("CREATE TABLE paper_fill_lot_links (ledger_trade_id text,fill_id text,lot_id text)")
        _write_source_row(writer, "old-cash-buy", side="BUY")
        _write_source_row(writer, "old-cash-sell")
        writer.commit()
    report = verify_committed_trade_evidence(market="cash", connect_factory=
        _reader_factory(dsn, options, [], reject_write=True))
    diagnostic = report["source_diagnostics"]
    assert diagnostic["totals"]["ledger_rows"] == 2
    assert diagnostic["totals"]["source_buy_rows"] == diagnostic["totals"]["source_sell_rows"] == 1
    assert diagnostic["recorded_link_coverage"] == {"status": "AVAILABLE",
        "source_rows_with_recorded_link": 0, "source_rows_without_recorded_link": 2,
        "sell_rows_with_recorded_link": 0, "sell_rows_without_recorded_link": 1}
    assert diagnostic["reason"] == "ALL_SOURCE_SELL_ROWS_LACK_RECORDED_LINKS"
    assert diagnostic["recorded_graph_coverage"]["status"] == "UNAVAILABLE"
    assert diagnostic["bounded_receipt_records"] == 1
    assert diagnostic["bounded_exact_linked_sell_records"] == 0
    assert report["records"][0]["canonical_fill_linkage"] == "UNAVAILABLE"
    assert report["records"][0]["brain_status"] == "PENDING"
    with psycopg.connect(dsn, options=options, row_factory=dict_row) as audit:
        assert audit.execute("SELECT COUNT(*) AS n FROM trade_ledger").fetchone()["n"] == 2
        assert audit.execute("SELECT COUNT(*) AS n FROM paper_fill_lot_links").fetchone()["n"] == 0
        assert audit.execute("SELECT COUNT(*) AS n FROM oracle_brain_episodes").fetchone()["n"] == 0
        assert audit.execute("SELECT COUNT(*) AS n FROM paper_regime_trade_metrics").fetchone()["n"] == 0


def test_postgres_cash_recorded_edges_are_coverage_not_exact_graph_validation(committed_evidence_db):
    import psycopg
    from psycopg.rows import dict_row
    from trade_evidence_verification import verify_committed_trade_evidence

    dsn, options = committed_evidence_db
    with psycopg.connect(dsn, options=options, row_factory=dict_row) as writer:
        writer.execute("CREATE TABLE paper_fill_lot_links (ledger_trade_id text,fill_id text,lot_id text)")
        writer.execute("CREATE TABLE paper_fills (fill_id text,order_id text,canonical_trade_id bigint,side text)")
        writer.execute("CREATE TABLE paper_orders (order_id text)")
        writer.execute("CREATE TABLE position_lots (lot_id text)")
        writer.execute("CREATE TABLE trades (id bigint)")
        _write_source_row(writer, "unlinked-buy", side="BUY")
        _write_source_row(writer, "dangling-sell")
        _write_source_row(writer, "incomplete-sell")
        writer.execute("""INSERT INTO paper_fill_lot_links VALUES
            ('dangling-sell','missing-fill','missing-lot-a'),
            ('incomplete-sell','known-fill','missing-lot-b')""")
        writer.execute("INSERT INTO paper_fills VALUES ('known-fill','missing-order',999,'SELL')")
        writer.commit()
    # Empty exact-ID selection requests only inventory; no historical ID is
    # inferred and the aggregate cannot award an exact receipt to any edge.
    report = verify_committed_trade_evidence([], market="cash", connect_factory=
        _reader_factory(dsn, options, [], reject_write=True))
    diagnostic = report["source_diagnostics"]
    assert report["records"] == []
    assert diagnostic["recorded_link_coverage"] == {"status": "AVAILABLE",
        "source_rows_with_recorded_link": 2, "source_rows_without_recorded_link": 1,
        "sell_rows_with_recorded_link": 2, "sell_rows_without_recorded_link": 0}
    assert diagnostic["recorded_graph_coverage"] == {"status": "AVAILABLE", "link_rows": 2,
        "link_rows_missing_fill": 1, "link_rows_missing_order": 1, "link_rows_missing_lot": 2,
        "link_rows_missing_execution": 1, "sell_link_rows_missing_recorded_buy_link": 1}
    assert diagnostic["reason"] == "SOURCE_ROWS_PRESENT_QUALIFICATION_NOT_INFERRED"
    assert diagnostic["qualification_status"] == "NOT_EVALUATED_BY_INVENTORY"
    assert diagnostic["bounded_exact_linked_sell_records"] == 0


def test_postgres_cash_inventory_uses_receipt_snapshot_during_concurrent_commit(committed_evidence_db):
    import psycopg
    from psycopg.rows import dict_row
    from trade_evidence_verification import verify_committed_trade_evidence

    dsn, options = committed_evidence_db
    with psycopg.connect(dsn, options=options, row_factory=dict_row) as writer:
        _write_source_row(writer, "old-stock-label", market="stocks")
        writer.commit()
        _write_source_row(writer, "later-cash-history")
        first = verify_committed_trade_evidence([], market="cash", connect_factory=
            _reader_factory(dsn, options, [], after_snapshot=writer.commit))
        second = verify_committed_trade_evidence([], market="cash", connect_factory=
            _reader_factory(dsn, options, []))
    assert first["source_diagnostics"]["totals"]["ledger_rows"] == 1
    assert first["source_diagnostics"]["totals"]["source_sell_rows"] == 0
    assert second["source_diagnostics"]["totals"]["ledger_rows"] == 2
    assert second["source_diagnostics"]["totals"]["source_sell_rows"] == 1
    assert first["snapshot"] != second["snapshot"]


def test_postgres_cash_inventory_output_is_bounded_without_truncating_totals(committed_evidence_db):
    import psycopg
    from psycopg.rows import dict_row
    from trade_evidence_verification import MAX_INVENTORY_GROUPS, verify_committed_trade_evidence

    dsn, options = committed_evidence_db
    with psycopg.connect(dsn, options=options, row_factory=dict_row) as writer:
        for market in ("cash", "stock", "CASH", "crypto"):
            for side in ("BUY", "SELL", "buy", "sell"):
                for broker in ("PAPER", "paper", "LIVE", "TEST", "SIMULATION"):
                    _write_source_row(writer, f"inventory-{market}-{side}-{broker}", market=market, side=side, broker=broker)
        writer.commit()
    diagnostic = verify_committed_trade_evidence([], market="cash", connect_factory=
        _reader_factory(dsn, options, []))["source_diagnostics"]
    assert diagnostic["totals"]["ledger_rows"] == diagnostic["inventory_group_count"] == 80
    assert diagnostic["inventory_truncated"] is True
    assert len(diagnostic["ledger_inventory"]) == diagnostic["inventory_group_limit"] == MAX_INVENTORY_GROUPS
    assert diagnostic["totals"]["source_buy_rows"] == diagnostic["totals"]["source_sell_rows"] == 1
