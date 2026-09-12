from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import sys

import paper_strategy_execution_guard as guard


def _fake_oracle(monkeypatch, *, open_position: bool, buy_age_minutes: float | None):
    now = datetime.now(timezone.utc)

    def row(sql, params=None):
        if "FROM positions" in sql:
            return {"symbol": "SOL-USD"} if open_position else None
        if "FROM trades" in sql:
            if buy_age_minutes is None:
                return {}
            return {"created_at": (now - timedelta(minutes=buy_age_minutes)).isoformat()}
        return None

    fake = SimpleNamespace(row=row)
    monkeypatch.setitem(sys.modules, "oracle_bot", fake)
    return fake


def test_recent_buy_into_open_position_is_rate_limited(monkeypatch):
    monkeypatch.setenv("PAPER_CRYPTO_OPEN_POSITION_ACCUMULATION_COOLDOWN_MINUTES", "5")
    _fake_oracle(monkeypatch, open_position=True, buy_age_minutes=1)

    allowed, reason = guard._open_position_accumulation_allows("SOL-USD")

    assert allowed is False
    assert reason.startswith("open_position_accumulation_cooldown:")
    assert reason.endswith("/5.00m")


def test_open_position_accumulation_allowed_after_interval(monkeypatch):
    monkeypatch.setenv("PAPER_CRYPTO_OPEN_POSITION_ACCUMULATION_COOLDOWN_MINUTES", "5")
    _fake_oracle(monkeypatch, open_position=True, buy_age_minutes=7)

    allowed, reason = guard._open_position_accumulation_allows("SOL-USD")

    assert allowed is True
    assert reason.startswith("open_position_accumulation_cooldown_elapsed:")


def test_first_entry_is_not_blocked(monkeypatch):
    monkeypatch.setenv("PAPER_CRYPTO_OPEN_POSITION_ACCUMULATION_COOLDOWN_MINUTES", "5")
    _fake_oracle(monkeypatch, open_position=False, buy_age_minutes=1)

    assert guard._open_position_accumulation_allows("SOL-USD") == (True, "no_open_position")


def test_final_buy_wrapper_blocks_repeated_buy_and_accumulate(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")

    calls = []

    def original_buy(*args, **kwargs):
        calls.append((args, kwargs))
        return True

    fake = SimpleNamespace(_buy=original_buy, row=lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "oracle_bot", fake)
    monkeypatch.setattr(guard.economics, "active", lambda: True)
    monkeypatch.setattr(
        guard,
        "_open_position_accumulation_allows",
        lambda symbol: (False, "open_position_accumulation_cooldown:1.00/5.00m"),
    )

    previous = guard._INSTALLED
    guard._INSTALLED = False
    try:
        assert guard.install_paper_strategy_execution_guard() is True
        for action in ("BUY", "ACCUMULATE"):
            result = fake._buy(
                "crypto",
                "SOL-USD",
                200.0,
                {"symbol": "SOL-USD", "action": action},
            )
            assert result is False
        assert calls == []
    finally:
        guard._INSTALLED = previous
