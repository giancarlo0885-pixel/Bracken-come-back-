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


def test_blocked_buy_result_matches_oracle_buy_contract():
    assert guard._blocked_buy("paper_guard_reason") == (False, "paper_guard_reason", None)


def test_final_buy_wrapper_blocks_repeated_buy_and_accumulate_with_contract(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")

    calls = []

    def original_buy(*args, **kwargs):
        calls.append((args, kwargs))
        return True, "original_buy", None

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
            assert result == (
                False,
                "open_position_accumulation_cooldown:1.00/5.00m",
                None,
            )
        assert calls == []
    finally:
        guard._INSTALLED = previous


def test_cumulative_tier_capacity_subtracts_open_cost_basis(monkeypatch):
    fake = SimpleNamespace(
        row=lambda *args, **kwargs: {
            "quantity": 0.2,
            "average_price": 150.0,
            "entry_price": 149.0,
            "current_price": 151.0,
        }
    )
    monkeypatch.setitem(sys.modules, "oracle_bot", fake)

    remaining, current = guard._remaining_cumulative_tier_capacity("AAVE-USD", 49.57)

    assert current == 30.0
    assert remaining == 19.57


def test_final_buy_wrapper_blocks_when_cumulative_research_cap_is_exhausted(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")

    calls = []

    def original_buy(*args, **kwargs):
        calls.append((args, kwargs))
        return True, "original_buy", None

    def row(sql, params=None):
        if "FROM positions" in sql:
            return {
                "quantity": 1.0,
                "average_price": 49.57,
                "entry_price": 49.57,
                "current_price": 49.57,
            }
        return None

    fake = SimpleNamespace(_buy=original_buy, row=row)
    scorecard = SimpleNamespace(size_multiplier=0.35)
    monkeypatch.setitem(sys.modules, "oracle_bot", fake)
    monkeypatch.setattr(guard.economics, "active", lambda: True)
    monkeypatch.setattr(
        guard,
        "_open_position_accumulation_allows",
        lambda symbol: (True, "open_position_accumulation_cooldown_elapsed:5.42/5.00m"),
    )
    monkeypatch.setattr(
        guard.economics,
        "fee_edge_allows_entry",
        lambda signal: (True, "edge_clears_round_trip_cost", 0.85, 0.55),
    )
    monkeypatch.setattr(
        guard.economics,
        "adjusted_optimizer_target",
        lambda signal, target: (49.57, scorecard, "research_tier_exploration_cap"),
    )
    monkeypatch.setattr(guard.economics, "log_economics", lambda *args, **kwargs: None)
    monkeypatch.setattr(guard.regime_gate, "regime_validation_ok", lambda signal: (True, "pass"))

    previous = guard._INSTALLED
    guard._INSTALLED = False
    try:
        assert guard.install_paper_strategy_execution_guard() is True
        result = fake._buy(
            "crypto",
            "AAVE-USD",
            155.0,
            {
                "symbol": "AAVE-USD",
                "action": "BUY",
                "v39_optimizer_approved_amount": 141.63,
            },
        )
        assert result == (False, "strategy_economics_zero_target", None)
        assert calls == []
    finally:
        guard._INSTALLED = previous


def test_fee_rejection_preserves_buy_contract(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")

    calls = []

    def original_buy(*args, **kwargs):
        calls.append((args, kwargs))
        return True, "original_buy", None

    fake = SimpleNamespace(_buy=original_buy, row=lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "oracle_bot", fake)
    monkeypatch.setattr(guard.economics, "active", lambda: True)
    monkeypatch.setattr(guard, "_open_position_accumulation_allows", lambda symbol: (True, "no_open_position"))
    monkeypatch.setattr(
        guard.economics,
        "fee_edge_allows_entry",
        lambda signal: (False, "edge_below_round_trip_cost:test", 0.1, 0.5),
    )

    previous = guard._INSTALLED
    guard._INSTALLED = False
    try:
        assert guard.install_paper_strategy_execution_guard() is True
        result = fake._buy("crypto", "SOL-USD", 200.0, {"symbol": "SOL-USD", "action": "BUY"})
        assert result == (False, "edge_below_round_trip_cost:test", None)
        assert calls == []
    finally:
        guard._INSTALLED = previous
