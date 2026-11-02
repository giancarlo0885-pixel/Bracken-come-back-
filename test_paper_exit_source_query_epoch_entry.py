"""A source close cannot qualify for a frozen model begun after its entry."""
import sqlite3

from paper_exit_research import source_query_diagnostics


class _SqlitePostgresAdapter:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, params=()):
        query = query.replace("COUNT(*)::int", "CAST(COUNT(*) AS INTEGER)")
        return self.connection.execute(query.replace("%s", "?"), params)


def _report(entry_time, exit_time):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE trade_ledger(
            trade_id TEXT, market TEXT, side TEXT,
            broker_mode TEXT, account_environment TEXT
        );
        CREATE TABLE paper_regime_trade_metrics(
            trade_id TEXT PRIMARY KEY, market TEXT,
            entry_time TEXT, exit_time TEXT,
            cost_provenance TEXT, round_trip_net_pnl REAL
        );
        CREATE TABLE garibaldi_shadow_exit_epochs(
            model_version TEXT PRIMARY KEY, started_at TEXT
        );
        INSERT INTO trade_ledger VALUES(
            'spanning', 'crypto', 'SELL', 'PAPER', 'PAPER'
        );
        INSERT INTO garibaldi_shadow_exit_epochs VALUES(
            'frozen-v1', '2026-10-10T00:00:00Z'
        );
    """)
    connection.execute(
        """INSERT INTO paper_regime_trade_metrics VALUES(
            'spanning', 'crypto', ?, ?, 'exact_lot', 1.0
        )""", (entry_time, exit_time)
    )
    return source_query_diagnostics(
        _SqlitePostgresAdapter(connection), "crypto", "frozen-v1"
    )


def test_entry_before_model_epoch_exit_after_must_not_qualify():
    assert _report(
        "2026-10-09T23:00:00Z", "2026-10-10T01:00:00Z"
    ) == {"before_model_epoch": 1}


def test_fully_pre_epoch_trade_remains_excluded():
    assert _report(
        "2026-10-09T22:00:00Z", "2026-10-09T23:00:00Z"
    ) == {"before_model_epoch": 1}


def test_entry_at_epoch_boundary_remains_eligible():
    assert _report(
        "2026-10-10T00:00:00Z", "2026-10-10T01:00:00Z"
    ) == {"eligible_source_close": 1}
