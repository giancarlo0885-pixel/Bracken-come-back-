"""Configured ledger diagnostics: read-only, bounded, never a data entitlement."""
from datetime import datetime, timedelta, timezone

import pytest

from provider_budget_diagnostics import (
    emit_public_research_budget_report,
    read_public_research_budgets,
)

NOW = datetime(2026, 10, 20, 17, 45, tzinfo=timezone.utc)
COINBASE = "Coinbase Public Research"
KRAKEN = "Kraken Public Research"


def row(provider=COINBASE, budget=2400, used=2400, remaining=0, cooldown=None):
    return {"provider": provider, "daily_budget": budget, "requests_used": used,
            "remaining_budget": remaining, "cooldown_until": cooldown}


class Reader:
    def __init__(self, rows=None, error=None):
        self.rows = rows if rows is not None else []
        self.error = error
        self.queries = []

    def execute(self, query, params):
        self.queries.append((query, params))
        if self.error:
            raise self.error
        return self

    def fetchall(self):
        return self.rows


def test_exhausted_quota_is_explicit_but_not_provider_entitlement(caplog):
    conn = Reader([row(), row(KRAKEN, 1200, 500, 700)])
    with caplog.at_level("INFO", logger="public-research-budget"):
        result = emit_public_research_budget_report(conn, now=NOW)
    assert result["status"] == "READ_ONLY_SNAPSHOT"
    assert result["provider_api_entitlements"] == "UNVERIFIED"
    assert result["providers"][COINBASE] == {
        "status": "CONFIGURED_BUDGET_EXHAUSTED",
        "configured_daily_budget": 2400, "requests_reserved": 2400,
        "remaining_reservations": 0, "cooldown_active": False,
    }
    assert result["providers"][KRAKEN]["status"] == "CONFIGURED_CAPACITY_REMAINING"
    assert result["providers"][KRAKEN]["remaining_reservations"] == 700
    assert result["execution_impact"] == "NONE"
    assert "PUBLIC RESEARCH BUDGET" in caplog.text
    assert "CONFIGURED_BUDGET_EXHAUSTED" in caplog.text
    assert len(conn.queries) == 1
    sql, args = conn.queries[0]
    assert sql.strip().startswith("SELECT ")
    assert "LIMIT 2" in sql and "__provider__" in sql
    assert "FOR UPDATE" not in sql
    assert args == ("2026-10-20",)


@pytest.mark.parametrize("cooldown,state", [
    ("2026-10-20T17:46:00Z", "CONFIGURED_COOLDOWN_ACTIVE"),
    ("2026-10-20T17:40:00Z", "CONFIGURED_CAPACITY_REMAINING"),
])
def test_cooldown_in_elected_ledger_is_safely_reported(cooldown, state):
    conn = Reader([row(used=100, remaining=2300, cooldown=cooldown)])
    result = read_public_research_budgets(conn, now=NOW)
    assert result["providers"][COINBASE]["status"] == state
    assert result["providers"][KRAKEN] == {"status": "LEDGER_ROW_ABSENT"}


def test_missing_rows_are_unknown_not_free_capacity():
    result = read_public_research_budgets(Reader(), now=NOW)
    assert all(v["status"] == "LEDGER_ROW_ABSENT"
               for v in result["providers"].values())


@pytest.mark.parametrize("bad", [
    row(used=2000, remaining=401),
    row(used=-1, remaining=2401),
    row(used="oops", remaining=2000),
    row(used=True, remaining=2399),
    row(used=1, remaining=2399, cooldown="not a date"),
    row(used=1, remaining=2399, cooldown="2026-10-20T23:00:00"),
])
def test_malformed_budget_cannot_be_described_as_available(bad):
    state = read_public_research_budgets(Reader([bad]), now=NOW)
    assert state["providers"][COINBASE] == {"status": "LEDGER_INCONSISTENT"}


def test_duplicate_or_unrecognized_rows_are_inconsistent():
    for rows in ([row(), row()], [row("unrecognized")]):
        result = read_public_research_budgets(Reader(rows), now=NOW)
        assert result["status"] == "LEDGER_INCONSISTENT"
        assert "providers" not in result


def test_ledger_error_does_not_log_sql_or_credentials(caplog):
    sensitive = RuntimeError("password=private; host=internal")
    conn = Reader(error=sensitive)
    with caplog.at_level("INFO", logger="public-research-budget"):
        result = emit_public_research_budget_report(conn, now=NOW)
    assert result["status"] == "LEDGER_UNAVAILABLE"
    assert result["error_class"] == "RuntimeError"
    assert "private" not in caplog.text
    assert "internal" not in caplog.text
    assert result["execution_impact"] == "NONE"


def test_budget_report_uses_utc_date_not_local_or_previous_day():
    local = NOW.astimezone(timezone(timedelta(hours=-7)))
    conn = Reader([row()])
    read_public_research_budgets(conn, now=local)
    assert conn.queries[0][1] == (NOW.date().isoformat(),)
