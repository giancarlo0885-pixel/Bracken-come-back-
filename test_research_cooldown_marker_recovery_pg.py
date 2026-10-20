"""PostgreSQL verifies atomic recovery of a same-day expired cooldown marker."""
from datetime import datetime, timedelta, timezone
import os
import pytest

import global_adaptive_engine as engine


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="PostgreSQL CI service required")
def test_postgres_public_research_cooldown_placeholder_recovers_only_after_expiry():
    import database

    now = datetime(2026, 10, 20, 19, 15, tzinfo=timezone.utc)
    provider = "Coinbase Public Research"
    cap = "__provider__"
    database.initialize_database()
    with database.connect() as conn:
        engine.ensure_v39_tables(conn)
        conn.execute(
            "DELETE FROM provider_budget_ledger WHERE provider=%s AND capability=%s AND utc_date=%s",
            (provider, cap, now.date().isoformat()),
        )
        engine.mark_provider_cooldown_db(
            conn, provider, seconds=30, capability=cap,
            reason="HTTP 429", now=now,
        )
        original = conn.execute(
            """SELECT daily_budget, requests_used, remaining_budget, data_mode, cooldown_until
               FROM provider_budget_ledger WHERE provider=%s AND capability=%s AND utc_date=%s""",
            (provider, cap, now.date().isoformat()),
        ).fetchone()
        assert (original["daily_budget"], original["requests_used"], original["remaining_budget"]) == (0, 0, 0)
        assert original["data_mode"] == "shared_cooldown"

        blocked = engine.reserve_provider_budget_db(
            conn, provider, cap, daily_budget=2400, entitlement="public",
            data_mode="shadow_research", now=now + timedelta(seconds=20),
        )
        assert blocked["reserved"] is False and "cooldown" in blocked["reason"]
        still = conn.execute(
            "SELECT daily_budget, requests_used FROM provider_budget_ledger WHERE provider=%s AND capability=%s AND utc_date=%s",
            (provider, cap, now.date().isoformat()),
        ).fetchone()
        assert still["daily_budget"] == 0 and still["requests_used"] == 0

        first = engine.reserve_provider_budget_db(
            conn, provider, cap, daily_budget=2400, entitlement="public",
            data_mode="shadow_research", now=now + timedelta(seconds=31),
        )
        assert first == {
            "reserved": True, "remaining": 2399, "provider": provider, "capability": cap
        }
        second = engine.reserve_provider_budget_db(
            conn, provider, cap, daily_budget=2400, entitlement="public",
            data_mode="shadow_research", now=now + timedelta(seconds=32),
        )
        assert second["reserved"] is True and second["remaining"] == 2398
        repaired = conn.execute(
            """SELECT daily_budget, requests_used, remaining_budget, data_mode,
                      last_failure, cooldown_until
               FROM provider_budget_ledger WHERE provider=%s AND capability=%s AND utc_date=%s""",
            (provider, cap, now.date().isoformat()),
        ).fetchone()
        assert repaired["daily_budget"] == 2400
        assert repaired["requests_used"] == 2
        assert repaired["remaining_budget"] == 2398
        assert repaired["data_mode"] == "shadow_research"
        assert repaired["last_failure"] == "HTTP 429"
        assert repaired["cooldown_until"] == original["cooldown_until"]
        conn.execute(
            "DELETE FROM provider_budget_ledger WHERE provider=%s AND capability=%s AND utc_date=%s",
            (provider, cap, now.date().isoformat()),
        )
