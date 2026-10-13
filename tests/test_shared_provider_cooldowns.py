"""Exercise shared cooldown SQL against an isolated in-memory ledger."""
from datetime import datetime, timedelta, timezone
import sqlite3

import global_adaptive_engine as engine


class _Ledger:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = lambda cursor, values: dict(zip((column[0] for column in cursor.description), values))
        self.conn.execute("""
            CREATE TABLE provider_budget_ledger (
                provider TEXT, capability TEXT, utc_date TEXT, entitlement TEXT,
                daily_budget INTEGER, requests_used INTEGER, remaining_budget INTEGER,
                last_failure TEXT, cooldown_until TEXT, data_mode TEXT, updated_at TEXT,
                UNIQUE(provider, capability, utc_date)
            )
        """)

    def execute(self, sql, params=()):
        return self.conn.execute(sql.replace("%s", "?"), params)


def test_shared_cooldown_survives_utc_midnight_and_expires():
    conn = _Ledger()
    start = datetime(2026, 10, 13, 23, 59, tzinfo=timezone.utc)
    engine.mark_provider_cooldown_db(conn, "Yahoo Finance", seconds=900, reason="429", now=start)

    other_worker = start + timedelta(minutes=2)
    active = engine.provider_cooldown_active_db(conn, "Yahoo Finance", now=other_worker)
    assert active["active"] is True
    assert active["reason"] == "429"
    assert engine.provider_cooldown_active_db(conn, "Polygon", now=other_worker)["active"] is False
    assert engine.provider_cooldown_active_db(conn, "Yahoo Finance", "quote", now=other_worker)["active"] is False
    assert engine.provider_cooldown_active_db(conn, "Yahoo Finance", now=start + timedelta(seconds=900))["active"] is False


def test_shorter_worker_cooldown_cannot_shorten_shared_recovery_window():
    conn = _Ledger()
    start = datetime(2026, 10, 13, 10, tzinfo=timezone.utc)
    engine.mark_provider_cooldown_db(conn, "Alpaca IEX", seconds=86400, reason="HTTP 401", now=start)
    engine.mark_provider_cooldown_db(conn, "Alpaca IEX", seconds=900, reason="HTTP 429", now=start + timedelta(seconds=1))

    active = engine.provider_cooldown_active_db(conn, "Alpaca IEX", now=start + timedelta(hours=1))
    assert active["active"] is True
    assert active["reason"] == "HTTP 401"
    assert active["cooldown_until"] == (start + timedelta(days=1)).isoformat()


def test_previous_day_longer_cooldown_is_not_hidden_by_new_daily_budget_row():
    conn = _Ledger()
    start = datetime(2026, 10, 13, 23, 59, tzinfo=timezone.utc)
    engine.mark_provider_cooldown_db(conn, "Alpaca IEX", seconds=86400, reason="HTTP 401", now=start)
    engine.mark_provider_cooldown_db(conn, "Alpaca IEX", seconds=60, reason="HTTP 429", now=start + timedelta(minutes=2))

    active = engine.provider_cooldown_active_db(conn, "Alpaca IEX", now=start + timedelta(hours=1))
    assert active["active"] is True
    assert active["reason"] == "HTTP 401"
    assert active["cooldown_until"] == (start + timedelta(days=1)).isoformat()
