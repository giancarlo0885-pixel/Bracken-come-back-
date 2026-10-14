"""Commit receipts must separate durable evidence from qualification and use."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

import trade_evidence_verification as verification


TRADE_ID = "unit-committed-closed-trade"
ENTRY = datetime(2026, 10, 14, 9, tzinfo=timezone.utc)
EXIT = ENTRY + timedelta(minutes=20)
CHECKED = EXIT + timedelta(minutes=1)


def _exact_rows(trade_id=TRADE_ID):
    identity = {"trade_id": trade_id, "market": "crypto", "symbol": "BTC-USD", "entry_time": ENTRY.isoformat(), "exit_time": EXIT.isoformat()}
    # A genuine loss is just as eligible for durable learning as a winner.
    snapshot = {"accounting_version": "exact_round_trip_v1", "cost_provenance": "exact_lot", "entry_signal_id": "unit-entry-signal", "net_pnl": -0.81, "fees": 0.23, "outcome": "negative"}
    return {
        "ledger": [{**identity, "side": "SELL", "broker_mode": "PAPER", "account_environment": "PAPER", "entry_signal_id": "unit-entry-signal", "feature_snapshot": {"momentum_5d": 0.12}}],
        "metrics": [{**identity, "round_trip_net_pnl": -0.81, "round_trip_fees": 0.23, "cost_provenance": "exact_lot"}],
        "brain": [{**identity, "episode_key": f"trade:{trade_id}", "net_pnl": -0.81, "fees": 0.23, "provenance_status": "exact", "feature_snapshot": {"momentum_5d": 0.12}, "outcome_snapshot": snapshot, "execution_impact": "NONE"}],
        "aeve": [{"trade_id": trade_id, "generation": 2}],
    }


def _receipt(rows, **availability):
    return verification._receipt(TRADE_ID, "crypto", rows, {"metrics": True, "brain": True, "aeve": True, **availability}, CHECKED)


def test_exact_committed_loss_is_materialized_without_claiming_profitability_or_decision_use():
    receipt = _receipt(_exact_rows())
    assert receipt["ledger_status"] == "COMMITTED"
    assert receipt["ledger_count"] == receipt["brain_count"] == 1
    assert receipt["brain_status"] == "MATERIALIZED_EXACT"
    assert receipt["round_trip_net_pnl"] == -0.81
    assert receipt["round_trip_fees"] == 0.23
    assert receipt["entry_signal_id"] == "unit-entry-signal"
    assert receipt["episode_key"] == f"trade:{TRADE_ID}"
    assert receipt["aeve_status"] == "COMMITTED"
    assert receipt["aeve_generations"] == [2]
    assert receipt["ranking_decision_consumption"] == "UNAVAILABLE"
    assert receipt["canonical_fill_linkage"] == "UNAVAILABLE"
    assert receipt["execution_impact"] == "NONE"


@pytest.mark.parametrize("table,field,value,status", [
    ("metrics", "cost_provenance", "legacy_fallback", "UNQUALIFIED"),
    ("metrics", "round_trip_net_pnl", None, "UNQUALIFIED"),
    ("metrics", "round_trip_net_pnl", float("nan"), "UNQUALIFIED"),
    ("metrics", "round_trip_net_pnl", float("inf"), "UNQUALIFIED"),
    ("metrics", "round_trip_fees", None, "UNQUALIFIED"),
    ("metrics", "round_trip_fees", -0.01, "UNQUALIFIED"),
    ("metrics", "round_trip_fees", float("inf"), "UNQUALIFIED"),
    ("metrics", "round_trip_net_pnl", True, "UNQUALIFIED"),
    ("metrics", "round_trip_fees", False, "UNQUALIFIED"),
    ("brain", "provenance_status", "legacy", "UNQUALIFIED"),
    ("brain", "execution_impact", "LIVE", "UNQUALIFIED"),
    ("ledger", "entry_signal_id", None, "UNQUALIFIED"),
    ("ledger", "feature_snapshot", {}, "UNQUALIFIED"),
    ("ledger", "feature_snapshot", "[]", "UNQUALIFIED"),
    ("ledger", "side", "BUY", "NOT_APPLICABLE"),
    ("ledger", "broker_mode", "LIVE", "NON_PAPER"),
    ("ledger", "account_environment", "LIVE", "NON_PAPER"),
])
def test_inexact_missing_or_nonpaper_evidence_never_gets_exact_receipt(table, field, value, status):
    rows = _exact_rows()
    rows[table][0][field] = value
    receipt = _receipt(rows)
    assert receipt["brain_status"] == status
    assert "round_trip_net_pnl" not in receipt


@pytest.mark.parametrize("field,value", [
    ("accounting_version", "legacy"),
    ("accounting_version", None),
    ("cost_provenance", "estimated"),
    ("cost_provenance", None),
])
def test_outcome_snapshot_requires_both_exact_accounting_markers(field, value):
    rows = _exact_rows()
    rows["brain"][0]["outcome_snapshot"][field] = value
    assert _receipt(rows)["brain_status"] == "UNQUALIFIED"


@pytest.mark.parametrize("table,field,value", [
    ("metrics", "market", "cash"),
    ("brain", "market", "cash"),
    ("metrics", "symbol", "ETH-USD"),
    ("brain", "symbol", "ETH-USD"),
    ("metrics", "entry_time", (ENTRY + timedelta(seconds=1)).isoformat()),
    ("brain", "exit_time", (EXIT + timedelta(seconds=1)).isoformat()),
    ("metrics", "entry_time", ENTRY.replace(tzinfo=None).isoformat()),
    ("brain", "episode_key", "trade:unrelated-trade"),
    ("brain", "net_pnl", -0.80),
    ("brain", "fees", 0.22),
])
def test_materialized_rows_must_match_canonical_scope_identity_time_and_costs(table, field, value):
    rows = _exact_rows()
    rows[table][0][field] = value
    assert _receipt(rows)["brain_status"] == "MISMATCH"


@pytest.mark.parametrize("field,value", [("entry_signal_id", "other-signal"), ("net_pnl", 10.0), ("fees", 0.0), ("net_pnl", True), ("fees", False), ("outcome", "positive"), ("outcome", "flat"), ("outcome", None)])
def test_snapshot_signal_and_costs_must_match_canonical_materialized_evidence(field, value):
    rows = _exact_rows()
    rows["brain"][0]["outcome_snapshot"][field] = value
    assert _receipt(rows)["brain_status"] == "MISMATCH"


@pytest.mark.parametrize("net,outcome", [(1.25, "positive"), (0.0, "flat"), (-1.25, "negative")])
def test_exact_outcome_label_agrees_with_post_cost_pnl_sign(net, outcome):
    rows = _exact_rows()
    rows["metrics"][0]["round_trip_net_pnl"] = net
    rows["brain"][0]["net_pnl"] = net
    rows["brain"][0]["outcome_snapshot"].update(net_pnl=net, outcome=outcome)
    receipt = _receipt(rows)
    assert receipt["brain_status"] == "MATERIALIZED_EXACT"
    assert receipt["round_trip_net_pnl"] == net


@pytest.mark.parametrize("features", [{"momentum_5d": 0.13}, {}, {"momentum_5d": 0.12, "look_ahead_feature": 1.0}])
def test_materialized_entry_features_cannot_drift_from_canonical_entry_snapshot(features):
    rows = _exact_rows()
    rows["brain"][0]["feature_snapshot"] = features
    assert _receipt(rows)["brain_status"] == "MISMATCH"


@pytest.mark.parametrize("canonical,materialized", [
    ({"flag": True}, {"flag": 1}),
    ({"flag": 0}, {"flag": False}),
    ({"nested": {"flag": True}}, {"nested": {"flag": 1}}),
    ({"nested": [{"flag": False}]}, {"nested": [{"flag": 0}]}),
    ({"values": [1, 2]}, {"values": [True, 2]}),
])
def test_nested_feature_boolean_cannot_be_materialized_as_a_numeric_value(canonical, materialized):
    rows = _exact_rows()
    rows["ledger"][0]["feature_snapshot"] = canonical
    rows["brain"][0]["feature_snapshot"] = materialized
    assert _receipt(rows)["brain_status"] == "MISMATCH"


def test_feature_numeric_int_float_equivalence_preserves_exact_provenance():
    rows = _exact_rows()
    rows["ledger"][0]["feature_snapshot"] = {"count": 1, "nested": [2, {"momentum": 3}]}
    rows["brain"][0]["feature_snapshot"] = {"count": 1.0, "nested": [2.0, {"momentum": 3.0}]}
    assert _receipt(rows)["brain_status"] == "MATERIALIZED_EXACT"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_materialized_and_canonical_features_cannot_claim_exact_provenance(value):
    rows = _exact_rows()
    rows["ledger"][0]["feature_snapshot"] = {"nested": [{"momentum": value}]}
    rows["brain"][0]["feature_snapshot"] = {"nested": [{"momentum": value}]}
    assert _receipt(rows)["brain_status"] != "MATERIALIZED_EXACT"


def test_open_trade_commit_is_not_reported_as_a_completed_learning_episode():
    rows = _exact_rows()
    rows["ledger"][0]["exit_time"] = None
    receipt = _receipt(rows)
    assert receipt["ledger_status"] == "COMMITTED"
    assert receipt["brain_status"] == "NOT_APPLICABLE"
    assert "round_trip_net_pnl" not in receipt


@pytest.mark.parametrize("entry,exit_", [
    (ENTRY.replace(tzinfo=None).isoformat(), EXIT.isoformat()),
    (ENTRY.isoformat(), EXIT.replace(tzinfo=None).isoformat()),
    (EXIT.isoformat(), ENTRY.isoformat()),
    (ENTRY.isoformat(), (CHECKED + timedelta(seconds=1)).isoformat()),
    ("invalid", EXIT.isoformat()),
])
def test_unknown_reversed_or_future_canonical_chronology_never_qualifies(entry, exit_):
    rows = _exact_rows()
    rows["ledger"][0].update(entry_time=entry, exit_time=exit_)
    assert _receipt(rows)["brain_status"] == "UNQUALIFIED"


def test_equivalent_aware_utc_times_and_json_snapshots_preserve_exact_receipt():
    rows = _exact_rows()
    offset = timezone(timedelta(hours=2))
    for table in ("metrics", "brain"):
        rows[table][0]["entry_time"] = ENTRY.astimezone(offset).isoformat()
        rows[table][0]["exit_time"] = EXIT.astimezone(offset).isoformat()
    rows["ledger"][0]["feature_snapshot"] = json.dumps(rows["ledger"][0]["feature_snapshot"])
    rows["brain"][0]["outcome_snapshot"] = json.dumps(rows["brain"][0]["outcome_snapshot"])
    assert _receipt(rows)["brain_status"] == "MATERIALIZED_EXACT"


def test_missing_ledger_is_reported_even_when_learning_rows_are_committed():
    rows = _exact_rows()
    rows["ledger"] = []
    receipt = _receipt(rows)
    assert receipt["ledger_status"] == "MISSING"
    assert receipt["brain_status"] == "ORPHANED"
    assert receipt["aeve_status"] == "COMMITTED"
    assert "round_trip_net_pnl" not in receipt


@pytest.mark.parametrize("table", ["ledger", "metrics", "brain"])
def test_duplicate_canonical_or_learning_rows_are_ambiguous(table):
    rows = _exact_rows()
    rows[table].append(deepcopy(rows[table][0]))
    receipt = _receipt(rows)
    assert receipt["brain_status"] == "AMBIGUOUS"
    if table == "ledger":
        assert receipt["ledger_status"] == "AMBIGUOUS"
    assert "round_trip_net_pnl" not in receipt


def test_requested_market_cannot_verify_another_market_trade():
    rows = _exact_rows()
    rows["ledger"][0]["market"] = "cash"
    receipt = _receipt(rows)
    assert receipt["ledger_status"] == "SCOPE_MISMATCH"
    assert receipt["brain_status"] == "MISMATCH"


@pytest.mark.parametrize("table,status", [("brain", "PENDING"), ("metrics", "UNQUALIFIED")])
def test_available_but_missing_materialization_does_not_claim_ingestion(table, status):
    rows = _exact_rows()
    rows[table] = []
    assert _receipt(rows)["brain_status"] == status


@pytest.mark.parametrize("table", ["brain", "metrics"])
def test_absent_optional_learning_schema_is_unavailable_not_missing(table):
    rows = _exact_rows()
    rows[table] = []
    assert _receipt(rows, **{table: False})["brain_status"] == "UNAVAILABLE"


def test_aeve_absent_schema_is_distinct_from_no_committed_generation_outcomes():
    rows = _exact_rows()
    rows["aeve"] = []
    assert _receipt(rows, aeve=False)["aeve_status"] == "UNAVAILABLE"
    assert _receipt(rows)["aeve_status"] == "MISSING"


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _ReadSnapshot:
    def __init__(self, *, rows=None, missing_tables=(), failure=None, read_only="on", exit_error=None):
        self.rows = rows if rows is not None else _exact_rows()
        self.missing_tables = set(missing_tables)
        self.failure = failure
        self.read_only = read_only
        self.exit_error = exit_error
        self.statements = []
        self.exited = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.exited = True
        if self.exit_error is not None:
            raise self.exit_error
        return False

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        normalized = " ".join(sql.split())
        assert normalized.startswith(("SELECT ", "SET ")), "receipt attempted a database mutation"
        if self.failure and self.failure in normalized:
            raise RuntimeError("private database failure detail must not be logged")
        if normalized.startswith("SET "):
            return _Cursor([])
        if "pg_current_snapshot()" in normalized:
            return _Cursor([{"checked_at": CHECKED, "snapshot": "700:705:702", "read_only": self.read_only}])
        if "to_regclass" in normalized:
            return _Cursor([{"relation": None if params[0] in self.missing_tables else params[0]}])
        if "WHERE trade_id=ANY(%s)" in normalized:
            for table, name in (("ledger", "trade_ledger"), ("metrics", "paper_regime_trade_metrics"), ("brain", "oracle_brain_episodes"), ("aeve", "paper_aeve_generation_outcomes")):
                if f"FROM {name} " in normalized:
                    return _Cursor([row for row in self.rows[table] if row["trade_id"] in params[0]])
        raise AssertionError(f"unexpected SQL: {normalized}")


def test_independent_read_only_snapshot_emits_exact_receipts_after_transaction_exit():
    conn = _ReadSnapshot()
    result = verification.verify_committed_trade_evidence([TRADE_ID], connect_factory=lambda: conn)
    assert conn.exited is True
    assert result["status"] == "ok"
    assert result["transaction_read_only"] is True
    assert result["snapshot"] == "700:705:702"
    assert result["records"][0]["brain_status"] == "MATERIALIZED_EXACT"
    assert conn.statements[0][0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"


@pytest.mark.parametrize("failed_query", ["to_regclass", "FROM trade_ledger", "FROM paper_regime_trade_metrics", "FROM oracle_brain_episodes", "FROM paper_aeve_generation_outcomes"])
def test_query_failure_cannot_return_a_missing_or_partial_commit_receipt(failed_query):
    conn = _ReadSnapshot(failure=failed_query)
    with pytest.raises(RuntimeError):
        verification.verify_committed_trade_evidence([TRADE_ID], connect_factory=lambda: conn)
    assert conn.exited is True


def test_failed_read_transaction_exit_cannot_return_a_committed_receipt():
    conn = _ReadSnapshot(exit_error=RuntimeError("connection closed before successful exit"))
    with pytest.raises(RuntimeError):
        verification.verify_committed_trade_evidence([TRADE_ID], connect_factory=lambda: conn)


def test_receipt_refuses_a_transaction_that_is_not_read_only():
    conn = _ReadSnapshot(read_only="off")
    with pytest.raises(RuntimeError, match="not read only"):
        verification.verify_committed_trade_evidence([TRADE_ID], connect_factory=lambda: conn)


def test_optional_tables_are_not_queried_when_schema_discovery_reports_absence():
    conn = _ReadSnapshot(missing_tables={"paper_regime_trade_metrics", "oracle_brain_episodes", "paper_aeve_generation_outcomes"})
    result = verification.verify_committed_trade_evidence([TRADE_ID], connect_factory=lambda: conn)
    receipt = result["records"][0]
    assert receipt["ledger_status"] == "COMMITTED"
    assert receipt["brain_status"] == receipt["aeve_status"] == "UNAVAILABLE"
    selects = [sql for sql, _ in conn.statements if "WHERE trade_id=ANY(%s)" in sql]
    assert len(selects) == 1
    assert "FROM trade_ledger" in selects[0]


def test_explicit_ids_are_deduplicated_bounded_and_parameterized_without_sql_interpolation():
    unusual = "unit'); DROP TABLE trade_ledger; --"
    conn = _ReadSnapshot()
    result = verification.verify_committed_trade_evidence([TRADE_ID, unusual, f" {TRADE_ID} "], connect_factory=lambda: conn)
    assert [row["trade_id"] for row in result["records"]] == [TRADE_ID, unusual]
    for sql, params in conn.statements:
        assert unusual not in sql
        if "WHERE trade_id=ANY(%s)" in sql:
            assert params == ([TRADE_ID, unusual],)
    assert result["records"][1]["ledger_status"] == "MISSING"


@pytest.mark.parametrize("ids", [TRADE_ID, b"trade", [""], ["   "], ["line\nbreak"], ["x" * 129], [None], [1], [True], [False], [{}], [[]], [f"trade-{number}" for number in range(verification.MAX_TRADE_IDS + 1)]])
def test_invalid_or_unbounded_trade_ids_fail_before_opening_database(ids):
    def forbidden_connect():
        raise AssertionError("invalid IDs must not open the database")

    with pytest.raises(ValueError):
        verification.verify_committed_trade_evidence(ids, connect_factory=forbidden_connect)


def test_maximum_allowed_ids_remain_available_without_an_unbounded_query():
    ids = [f"unit-trade-{number}" for number in range(verification.MAX_TRADE_IDS)]
    conn = _ReadSnapshot(rows={key: [] for key in ("ledger", "metrics", "brain", "aeve")})
    result = verification.verify_committed_trade_evidence(ids, connect_factory=lambda: conn)
    assert len(result["records"]) == verification.MAX_TRADE_IDS
    assert all(row["ledger_status"] == "MISSING" for row in result["records"])


def test_worker_emitter_reports_sql_failure_as_unavailable_without_sensitive_error_detail(monkeypatch, caplog):
    def failing(*args, **kwargs):
        raise RuntimeError("private database failure detail must not be logged")

    monkeypatch.setattr(verification, "verify_committed_trade_evidence", failing)
    result = verification.emit_committed_trade_verification("crypto")
    assert result == {"status": "unavailable", "market": "crypto", "execution_impact": "NONE"}
    assert "status=UNAVAILABLE" in caplog.text
    assert "reason=RuntimeError" in caplog.text
    assert "private database failure" not in caplog.text
    assert "MISSING" not in caplog.text


@pytest.mark.parametrize("raises", [True, False])
def test_verification_hook_failure_cannot_downgrade_a_successful_worker_learning_sync(monkeypatch, raises):
    import market_worker
    import oracle_brain_learning

    completed = {"status": "ok", "market": "crypto", "episodes_processed": 1, "validation": {"market": "crypto"}, "advanced_learning": {"market": "crypto"}, "execution_impact": "NONE"}
    observed = []
    monkeypatch.setattr(oracle_brain_learning, "sync_brain_learning", lambda market: completed)

    def unavailable(market):
        observed.append(market)
        if raises:
            raise RuntimeError("verification hook failed")
        return {"status": "unavailable", "market": market, "execution_impact": "NONE"}

    monkeypatch.setattr(verification, "emit_committed_trade_verification", unavailable)
    assert market_worker._run_brain_learning_sync("crypto") is completed
    assert observed == ["crypto"]


def test_failed_learning_sync_does_not_emit_a_successful_commit_verification(monkeypatch):
    import market_worker
    import oracle_brain_learning

    def failing_sync(market):
        raise RuntimeError("learning transaction failed")

    monkeypatch.setattr(oracle_brain_learning, "sync_brain_learning", failing_sync)
    monkeypatch.setattr(verification, "emit_committed_trade_verification", lambda *args: (_ for _ in ()).throw(AssertionError("verification must follow successful sync")))
    result = market_worker._run_brain_learning_sync("crypto")
    assert result["status"] == "unavailable"
