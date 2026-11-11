"""Regression: missing canonical position evidence cannot authorize repeated paper accumulation.

All tests use isolated synthetic database readers. They do not query or change production.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import sys

import pytest

from paper_strategy_execution_guard import _open_position_accumulation_allows


def _install_reader(monkeypatch, reader):
    monkeypatch.setenv("PAPER_CRYPTO_OPEN_POSITION_ACCUMULATION_COOLDOWN_MINUTES", "5")
    monkeypatch.setitem(sys.modules, "oracle_bot", SimpleNamespace(row=reader))


def test_position_lookup_failure_does_not_bypass_accumulation_cooldown(monkeypatch):
    def reader(sql, params=None):
        raise RuntimeError("synthetic database outage")

    _install_reader(monkeypatch, reader)
    assert _open_position_accumulation_allows("BTC-USD") == (
        False, "open_position_state_unavailable"
    )


def test_buy_lookup_failure_for_existing_position_blocks(monkeypatch):
    def reader(sql, params=None):
        if "FROM positions" in sql:
            return {"symbol": "BTC-USD"}
        raise TimeoutError("synthetic trade ledger timeout")

    _install_reader(monkeypatch, reader)
    assert _open_position_accumulation_allows("BTC-USD") == (
        False, "open_position_state_unavailable"
    )


@pytest.mark.parametrize("buy_row", [{}, {"created_at": None}, {"created_at": "not-a-timestamp"}])
def test_existing_position_without_valid_last_buy_time_blocks(monkeypatch, buy_row):
    def reader(sql, params=None):
        return {"symbol": "BTC-USD"} if "FROM positions" in sql else buy_row

    _install_reader(monkeypatch, reader)
    assert _open_position_accumulation_allows("BTC-USD") == (
        False, "open_position_buy_timestamp_unavailable"
    )


def test_verified_first_entry_and_elapsed_cooldown_remain_allowed(monkeypatch):
    def no_position(sql, params=None):
        return None

    _install_reader(monkeypatch, no_position)
    assert _open_position_accumulation_allows("BTC-USD") == (True, "no_open_position")

    last_buy = datetime.now(timezone.utc) - timedelta(minutes=8)

    def elapsed(sql, params=None):
        if "FROM positions" in sql:
            return {"symbol": "BTC-USD"}
        return {"created_at": last_buy.isoformat()}

    _install_reader(monkeypatch, elapsed)
    allowed, reason = _open_position_accumulation_allows("BTC-USD")
    assert allowed is True
    assert reason.startswith("open_position_accumulation_cooldown_elapsed:")
