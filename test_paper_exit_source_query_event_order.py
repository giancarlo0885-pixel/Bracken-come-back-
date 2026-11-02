"""Shadow source eligibility must reject a close dated before its entry.

Exercise the actual SQL through SQLite with PostgreSQL placeholder adaptation;
no production database writes, trade records, or model cutoffs are touched.
"""
import sqlite3

from paper_exit_research import source_query_diagnostics


class _SqlitePostgresAdapter:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, params=()):
        query = query.replace("COUNT(*)::int", "CAST(COUNT(*) AS INTEGER)")
        return self.connection.execute(query.replace("%s", "?"), params)


def _diagnostics(entry_time, exit_time):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript("""
            CREATE TABLE trade_ledger(
                trade_id TEXT, market TEXT, side TEXT,
                broker_mode TEXT, account_environment TEXT
            );
            CREATE TABLE paper_regime_trade_metrics(
                trade_id TEXT, market TEXT, entry_time TEXT, exit_time TEXT,
                cost_provenance TEXT, round_trip_net_pnl REAL
            );
            CREATE TABLE garibaldi_shadow_exit_epochs(
                model_version TEXT, started_at TEXT
            );
            INSERT INTO trade_ledger VALUES(
                'reverse', 'crypto', 'SELL', 'PAPER', 'PAPER'
            );
            INSERT INTO garibaldi_shadow_exit_epochs VALUES(
                'frozen-v1', '2026-10-10T00:00:00Z'
            );
        """)
        connection.execute(
            """INSERT INTO paper_regime_trade_metrics VALUES(
                'reverse', 'crypto', ?, ?, 'exact_lot', 0.5
            )""", (entry_time, exit_time)
        )
        return source_query_diagnostics(
            _SqlitePostgresAdapter(connection), "crypto", "frozen-v1"
        )
    finally:
        connection.close()


def test_reversed_entry_exit_times_never_count_as_eligible_source_close():
    assert _diagnostics(
        "2026-10-10T02:00:00Z", "2026-10-10T01:00:00Z"
    ) == {"invalid_event_order": 1}


def test_forward_entry_exit_order_still_qualifies():
    assert _diagnostics(
        "2026-10-10T01:00:00Z", "2026-10-10T02:00:00Z"
    ) == {"eligible_source_close": 1}


def test_pre_epoch_exclusion_precedence_is_unchanged():
    assert _diagnostics(
        "2026-10-09T23:00:00Z", "2026-10-10T02:00:00Z"
    ) == {"before_model_epoch": 1}
