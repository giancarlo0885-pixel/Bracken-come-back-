"""Regression: diagnostics must not attribute another market's metrics to a SELL.

The SQLite adapter translates only PostgreSQL's COUNT cast and parameter markers;
the JOINs, filters, and CASE expressions run unchanged on a real SQL engine.
"""
import sqlite3

from paper_exit_research import source_query_diagnostics


class _PgCompatibleSqlite:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, statement, params=()):
        statement = statement.replace("COUNT(*)::int", "CAST(COUNT(*) AS INTEGER)")
        return self.connection.execute(statement.replace("%s", "?"), params)


def _connection():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE trade_ledger (
            trade_id TEXT, market TEXT, side TEXT,
            broker_mode TEXT, account_environment TEXT
        );
        CREATE TABLE paper_regime_trade_metrics (
            trade_id TEXT, market TEXT, entry_time TEXT, exit_time TEXT,
            cost_provenance TEXT, round_trip_net_pnl REAL
        );
        CREATE TABLE garibaldi_shadow_exit_epochs (
            model_version TEXT, started_at TEXT
        );
        INSERT INTO garibaldi_shadow_exit_epochs
            VALUES ('frozen-v1', '2026-10-01T00:00:00Z');
        INSERT INTO trade_ledger VALUES
            ('shared-id', 'crypto', 'SELL', 'PAPER', 'PAPER'),
            ('cash-only', 'cash', 'SELL', 'PAPER', 'PAPER');
    """)
    return connection


def _metric(connection, market, trade_id="shared-id"):
    connection.execute("""
        INSERT INTO paper_regime_trade_metrics
        (trade_id, market, entry_time, exit_time, cost_provenance, round_trip_net_pnl)
        VALUES (?, ?, '2026-10-20T00:00:00Z', '2026-10-21T00:00:00Z', 'exact_lot', -1.0)
    """, (trade_id, market))


def test_cross_market_metric_cannot_qualify_crypto_close():
    connection = _connection()
    _metric(connection, "cash")
    report = source_query_diagnostics(_PgCompatibleSqlite(connection), "crypto", "frozen-v1")
    assert report == {"missing_regime_metric": 1}


def test_cross_market_duplicate_trade_id_cannot_double_count_crypto_close():
    connection = _connection()
    _metric(connection, "cash")
    _metric(connection, "crypto")
    report = source_query_diagnostics(_PgCompatibleSqlite(connection), "crypto", "frozen-v1")
    assert report == {"eligible_source_close": 1}


def test_matching_market_metric_retains_original_eligibility_classification():
    connection = _connection()
    _metric(connection, "crypto")
    report = source_query_diagnostics(_PgCompatibleSqlite(connection), "crypto", "frozen-v1")
    assert report == {"eligible_source_close": 1}
