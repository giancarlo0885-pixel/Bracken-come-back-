"""Strict quota-safety tests for recovered *expired* public research markers."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from global_adaptive_engine import reserve_provider_budget_db


NOW = datetime(2026, 10, 20, 19, 15, tzinfo=timezone.utc)


def row(provider="Coinbase Public Research", *, cooldown=None, mode="shared_cooldown",
        entitlement="configured", budget=0, used=0, remaining=0):
    return {"provider": provider, "capability": "__provider__",
            "cooldown_until": cooldown if cooldown is not None
                else (NOW - timedelta(minutes=1)).isoformat(),
            "updated_at": (NOW - timedelta(minutes=6)).isoformat(),
            "data_mode": mode, "entitlement": entitlement,
            "daily_budget": budget, "requests_used": used, "remaining_budget": remaining,
            "last_failure": "HTTP 429"}


class Ledger:
    def __init__(self, record, *, repair_count=1):
        self.record = record
        self.calls = []
        self.repair_count = repair_count

    def execute(self, sql, args):
        self.calls.append((sql, args))
        if "FOR UPDATE" in sql:
            return SimpleNamespace(fetchone=lambda: self.record)
        if "SET daily_budget=%s" in sql:
            return SimpleNamespace(rowcount=self.repair_count)
        return SimpleNamespace(rowcount=1)


def statements(conn):
    return [q for q, _ in conn.calls]


@pytest.mark.parametrize("provider,limit", [
    ("Coinbase Public Research", 2400),
    ("Kraken Public Research", 1200),
])
def test_expired_original_cooldown_marker_recovers_only_configured_budget(provider, limit):
    conn = Ledger(row(provider))
    result = reserve_provider_budget_db(
        conn, provider, "__provider__", daily_budget=limit,
        entitlement="public", data_mode="shadow_research", now=NOW)
    assert result["reserved"] is True
    assert result["remaining"] == limit - 1
    assert len(conn.calls) == 4
    assert "ON CONFLICT" in conn.calls[0][0]
    assert "FOR UPDATE" in conn.calls[1][0]
    sql, args = conn.calls[2]
    assert "SET daily_budget=%s" in sql and "remaining_budget=%s" in sql
    assert "AND data_mode='shared_cooldown'" in sql
    assert "AND cooldown_until=%s" in sql
    assert args[0:2] == (limit, limit)
    assert args[4:7] == (provider, "__provider__", NOW.date().isoformat())
    assert conn.calls[3][1][1:4] == (provider, "__provider__", NOW.date().isoformat())
    assert "remaining_budget=remaining_budget-1" in conn.calls[3][0]


@pytest.mark.parametrize("changes", [
    {"cooldown": (NOW + timedelta(minutes=5)).isoformat()},
    {"mode": "shadow_research"},
    {"entitlement": "unknown"},
    {"budget": 2400, "used": 2400, "remaining": 0},
    {"budget": 2400, "used": 0, "remaining": 0},
    {"budget": 0, "used": 1, "remaining": 0},
    {"budget": 0, "used": 0, "remaining": 1},
])
def test_cannot_replenish_actual_consumption_or_active_nonmarker_or_inconsistent_row(changes):
    conn = Ledger(row(**changes))
    result = reserve_provider_budget_db(
        conn, "Coinbase Public Research", "__provider__", daily_budget=2400,
        entitlement="public", data_mode="shadow_research", now=NOW)
    assert result["reserved"] is False
    assert not any("SET daily_budget=%s" in sql for sql in statements(conn))


@pytest.mark.parametrize("provider,capability,limit", [
    ("Alpaca IEX", "__provider__", 2400),
    ("Coinbase Public Research", "daily_history", 2400),
    ("Kraken Public Research", "__provider__", 2400),
    ("Coinbase Public Research", "__provider__", 1200),
    ("Coinbase Public Research", "__provider__", 0),
])
def test_wrong_scope_or_allowance_can_never_reinitialize_marker(provider, capability, limit):
    conn = Ledger(row(provider))
    result = reserve_provider_budget_db(
        conn, provider, capability, daily_budget=limit,
        entitlement="public", data_mode="shadow_research", now=NOW)
    assert result["reserved"] is False
    assert not any("SET daily_budget=%s" in sql for sql in statements(conn))


def test_failed_conditional_update_denies_request_and_does_not_reserve():
    conn = Ledger(row(), repair_count=0)
    result = reserve_provider_budget_db(
        conn, "Coinbase Public Research", "__provider__", daily_budget=2400,
        entitlement="public", data_mode="shadow_research", now=NOW)
    assert result == {
        "reserved": False, "reason": "provider cooldown marker recovery unavailable"
    }
    assert len(conn.calls) == 3
    assert "remaining_budget=remaining_budget-1" not in "".join(statements(conn))
