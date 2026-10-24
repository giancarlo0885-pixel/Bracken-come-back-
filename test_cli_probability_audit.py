"""Unit tests for the standalone, no-write historical evidence audit."""
import importlib.util
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "cli_probability_audit",
    Path(__file__).parent / "tools" / "cli_probability_audit.py",
)
audit = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(audit)


@pytest.mark.parametrize("value,category", [
    (None, "missing_null"),
    (0, "valid_0_to_1"),
    (0.5, "valid_0_to_1"),
    ("1.0", "valid_0_to_1"),
    (1.01, "percentage_gt1_le100"),
    (75, "percentage_gt1_le100"),
    ("100", "percentage_gt1_le100"),
    (-.1, "malformed_out_of_bounds"),
    (101, "malformed_out_of_bounds"),
    ("75%", "malformed_out_of_bounds"),
    (True, "malformed_out_of_bounds"),
    (float("nan"), "nonfinite"),
    (float("inf"), "nonfinite"),
])
def test_evidence_classification(value, category):
    assert audit.classify_probability(value) == category


def test_summary_partitions_every_input_once():
    values = [None, 0.5, 75, -1, float("nan")]
    result = audit.summarize_probabilities(values)
    assert sum(result.values()) == len(values)
    assert set(result) == set(audit.CATEGORIES)


@pytest.mark.parametrize("kwargs", [
    {"days": 0}, {"days": 366}, {"batch_size": 0},
    {"batch_size": 5001}, {"max_rows": 0}, {"timeout_ms": 999},
    {"timeout_ms": 30001},
])
def test_invalid_configuration_fails_before_database_connection(kwargs):
    with pytest.raises(ValueError):
        audit.audit_database("postgresql://invalid", **kwargs)


@pytest.mark.parametrize("row_count", [0, 1, 2, 3])
def test_audit_reports_truncation_only_when_an_extra_row_exists(monkeypatch, row_count):
    import psycopg

    statements = []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, params=()): statements.append((sql, params))
        def __iter__(self): return iter([(.5,)] * row_count)

    class Connection:
        rolled_back = False
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def cursor(self, **kwargs): return Cursor()
        def rollback(self): self.rolled_back = True

    connection = Connection()
    monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs: connection)
    result = audit.audit_database("unused", max_rows=2)
    assert result["total_records_evaluated"] == min(row_count, 2)
    assert result["sample_capped"] is (row_count > 2)
    assert result["evidence_classification_summary"]["valid_0_to_1"] == min(row_count, 2)
    assert connection.rolled_back
    assert "READ ONLY" in statements[0][0]


def test_audit_window_excludes_future_dated_evidence(monkeypatch):
    import psycopg

    queries = []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, params=()): queries.append((sql, params))
        def __iter__(self): return iter([])

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def cursor(self, **kwargs): return Cursor()
        def rollback(self): pass

    monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs: Connection())
    result = audit.audit_database("unused", days=7, max_rows=2)
    sql, params = queries[-1]
    assert "created_at::timestamptz <= %s" in sql
    cutoff, as_of, limit = params
    assert as_of - cutoff == timedelta(days=7)
    assert limit == 3 and result["as_of"] == as_of.isoformat()


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="Disposable PostgreSQL required")
@pytest.mark.parametrize("row_count", [0, 1, 2, 3])
def test_postgres_probability_window_and_exact_limit(monkeypatch, row_count):
    import psycopg
    from psycopg import sql

    connect = psycopg.connect
    dsn = os.environ["DATABASE_URL"]
    schema = "probability_audit_" + uuid4().hex
    now = datetime.now(timezone.utc)
    with connect(dsn) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        conn.execute(sql.SQL("CREATE TABLE {}.forecasts (id INTEGER, created_at TEXT, probability_up DOUBLE PRECISION)").format(sql.Identifier(schema)))
        for index in range(row_count):
            conn.execute(sql.SQL("INSERT INTO {}.forecasts VALUES (%s,%s,.5)").format(sql.Identifier(schema)),
                         (index, (now - timedelta(minutes=1)).isoformat()))
        # A future row sorts first by id and must neither count nor trigger truncation.
        conn.execute(sql.SQL("INSERT INTO {}.forecasts VALUES (1000,%s,75)").format(sql.Identifier(schema)),
                     ((now + timedelta(days=1)).isoformat(),))
    try:
        monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs:
                            connect(*args, options="-csearch_path=" + schema, **kwargs))
        result = audit.audit_database(dsn, max_rows=2)
        assert result["total_records_evaluated"] == min(row_count, 2)
        assert result["sample_capped"] is (row_count > 2)
        assert result["evidence_classification_summary"]["percentage_gt1_le100"] == 0
        with connect(dsn) as conn:
            count = conn.execute(sql.SQL("SELECT COUNT(*) FROM {}.forecasts").format(sql.Identifier(schema))).fetchone()[0]
            assert count == row_count + 1
    finally:
        with connect(dsn) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
