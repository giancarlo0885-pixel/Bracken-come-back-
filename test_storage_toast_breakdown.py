"""Read-only PostgreSQL storage attribution; protected evidence is never rewritten."""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest

import database


def test_storage_report_attributes_toast_and_residual_without_writes(monkeypatch):
    statements = []

    class FakeConn:
        def execute(self, sql, params=()):
            statements.append(str(sql))
            if "pg_database_size" in sql:
                return SimpleNamespace(fetchone=lambda: {"name": "unit", "bytes": 16000})
            return SimpleNamespace(fetchall=lambda: [
                {"table": "oracle_decision_replays", "total_bytes": 12000,
                 "table_bytes": 1024, "index_bytes": 2048,
                 "toast_bytes": 8192, "toast_index_bytes": 512,
                 "live_rows": 2, "dead_rows": 0},
                {"table": "no_toast", "total_bytes": 3072,
                 "table_bytes": 2048, "index_bytes": 1024,
                 "toast_bytes": 0, "toast_index_bytes": 0,
                 "live_rows": 1, "dead_rows": 0},
            ])

    class Ctx:
        def __enter__(self):
            return FakeConn()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(database, "connect", lambda: Ctx())
    report = database.database_storage_report()
    replay, no_toast = report["largest_tables"]
    assert replay["toast_bytes"] == 8192
    assert replay["toast_index_bytes"] == 512
    assert replay["other_bytes"] == 224
    assert replay["toast_size"] == "8.0KB"
    assert no_toast["toast_bytes"] == no_toast["toast_index_bytes"] == no_toast["other_bytes"] == 0
    assert "c.reltoastrelid <> 0" in statements[1]
    assert "pg_relation_size(c.reltoastrelid)" in statements[1]
    assert "pg_indexes_size(c.reltoastrelid)" in statements[1]
    assert len(statements) == 2
    assert all("DELETE" not in sql.upper() and "UPDATE" not in sql.upper() and "TRUNCATE" not in sql.upper() for sql in statements)


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="Disposable PostgreSQL CI service required")
def test_postgres_storage_report_attributes_real_toast_without_touching_replays():
    name = "oracle_storage_toast_probe_" + uuid.uuid4().hex[:12]
    with database.connect() as conn:
        conn.execute(f'CREATE TABLE public."{name}" (id bigint PRIMARY KEY, payload text)')
    try:
        with database.connect() as conn:
            conn.execute(
                f'INSERT INTO public."{name}" (id, payload) '
                "SELECT 1, string_agg(md5(g::text || random()::text), '') "
                "FROM generate_series(1, 600) AS g"
            )
        report = database.database_storage_report(limit=10000)
        row = next(item for item in report["largest_tables"] if item["table"] == name)
        assert row["toast_bytes"] > 0
        assert row["toast_index_bytes"] > 0
        assert row["total_bytes"] == (
            row["table_bytes"] + row["index_bytes"] + row["toast_bytes"]
            + row["toast_index_bytes"] + row["other_bytes"]
        )
        assert row["other_bytes"] >= 0
    finally:
        with database.connect() as conn:
            conn.execute(f'DROP TABLE IF EXISTS public."{name}"')
