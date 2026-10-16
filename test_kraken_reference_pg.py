"""Actual PostgreSQL election and bounded state; local disposable schemas only."""
from contextlib import contextmanager
from datetime import timedelta
import os
import time
import uuid

import pytest

from kraken_reference_runtime import STATE_DDL, claim_owner, write_state, display_state
from test_kraken_reference_book import NOW, ready


@pytest.fixture
def pg():
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict
    from psycopg.rows import dict_row
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn: pytest.skip("PostgreSQL integration only")
    settings = conninfo_to_dict(dsn)
    if (settings.get("host") not in {"localhost", "127.0.0.1", "::1"}
            or not (settings.get("dbname", "").endswith("_test") or settings.get("dbname", "").startswith("test_"))):
        pytest.skip("Disposable local test database required")
    schema = "kraken_test_" + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    @contextmanager
    def connection():
        with psycopg.connect(dsn, options="-c search_path="+schema+",pg_catalog", row_factory=dict_row, autocommit=True) as conn:
            yield conn
    try:
        with connection() as conn: conn.execute(STATE_DDL)
        yield connection
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_one_session_owns_feed_and_disconnect_releases_lock(pg):
    with pg() as first, pg() as second:
        assert claim_owner(first)
        assert not claim_owner(second)
        first.close()  # Model owner process/DB connection failure, no explicit unlock.
        # Server observes socket closure asynchronously; use a bounded handoff wait.
        deadline = time.monotonic() + 2
        while not claim_owner(second):
            assert time.monotonic() < deadline
            time.sleep(0.01)


def test_latest_state_is_singleton_and_reader_cannot_reanimate_stale_data(pg):
    payload = ready().health(NOW)
    with pg() as conn:
        for i in range(20): write_state(conn, payload, NOW+timedelta(seconds=i))
        assert conn.execute("SELECT count(*) AS n FROM kraken_reference_state").fetchone()["n"] == 1
    with pg() as reader:
        reader.execute("BEGIN READ ONLY")
        row = reader.execute("SELECT * FROM kraken_reference_state").fetchone()
        assert not display_state(row["payload"], row["updated_at"], NOW+timedelta(seconds=20))["quotes"]
        with pytest.raises(Exception): write_state(reader, payload, NOW)
        reader.execute("ROLLBACK")


def test_singleton_constraint_prevents_tick_history_growth(pg):
    import psycopg
    with pg() as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO kraken_reference_state VALUES (2,%s,'{}')", (NOW,))
