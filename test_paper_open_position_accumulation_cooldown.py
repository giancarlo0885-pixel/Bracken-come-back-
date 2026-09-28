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



def test_mature_losing_research_requires_two_distinct_verified_scans(monkeypatch):
    guard._RESEARCH_ENTRY_CONFIRMATIONS.clear()
    scorecard = SimpleNamespace(
        model_tier="RESEARCH",
        sample_count=100,
        expectancy=-0.21,
        profit_factor=0.19,
    )
    signal = {"symbol": "AAVE-USD", "action": "BUY"}

    first = guard._research_entry_confirmation_allows(
        "AAVE-USD",
        signal,
        {"quote_timestamp": "2026-09-28T16:00:00Z"},
        0.90,
        0.55,
        scorecard,
    )
    duplicate = guard._research_entry_confirmation_allows(
        "AAVE-USD",
        signal,
        {"quote_timestamp": "2026-09-28T16:00:00Z"},
        0.92,
        0.55,
        scorecard,
    )
    second = guard._research_entry_confirmation_allows(
        "AAVE-USD",
        signal,
        {"quote_timestamp": "2026-09-28T16:05:00Z"},
        0.88,
        0.55,
        scorecard,
    )

    assert first == (False, "research_entry_confirmation_pending:1/2")
    assert duplicate == (False, "research_entry_confirmation_pending:1/2")
    assert second == (True, "research_entry_confirmation_passed:2/2")


def test_research_confirmation_requires_positive_post_cost_edge():
    guard._RESEARCH_ENTRY_CONFIRMATIONS.clear()
    scorecard = SimpleNamespace(
        model_tier="RESEARCH",
        sample_count=100,
        expectancy=-0.21,
        profit_factor=0.19,
    )

    allowed, reason = guard._research_entry_confirmation_allows(
        "LINK-USD",
        {"symbol": "LINK-USD", "action": "BUY"},
        {"quote_timestamp": "2026-09-28T16:00:00Z"},
        0.50,
        0.55,
        scorecard,
    )

    assert allowed is False
    assert reason == "research_entry_confirmation_requires_positive_post_cost_edge"


def test_positive_or_immature_strategy_bypasses_two_scan_confirmation():
    guard._RESEARCH_ENTRY_CONFIRMATIONS.clear()
    positive = SimpleNamespace(
        model_tier="RESEARCH",
        sample_count=100,
        expectancy=0.10,
        profit_factor=1.20,
    )
    immature = SimpleNamespace(
        model_tier="RESEARCH",
        sample_count=10,
        expectancy=-0.10,
        profit_factor=0.50,
    )

    assert guard._research_entry_confirmation_allows(
        "SOL-USD",
        {"symbol": "SOL-USD", "action": "BUY"},
        {},
        0.80,
        0.55,
        positive,
    ) == (True, "research_entry_confirmation_not_required")
    assert guard._research_entry_confirmation_allows(
        "SOL-USD",
        {"symbol": "SOL-USD", "action": "BUY"},
        {},
        0.80,
        0.55,
        immature,
    ) == (True, "research_entry_confirmation_not_required")


def test_final_buy_wrapper_waits_for_second_research_scan(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")
    guard._RESEARCH_ENTRY_CONFIRMATIONS.clear()

    calls = []

    def original_buy(*args, **kwargs):
        calls.append((args, kwargs))
        return True, "original_buy", None

    fake = SimpleNamespace(_buy=original_buy, row=lambda *args, **kwargs: None)
    scorecard = SimpleNamespace(
        size_multiplier=0.35,
        model_tier="RESEARCH",
        sample_count=100,
        expectancy=-0.21,
        profit_factor=0.19,
    )
    monkeypatch.setitem(sys.modules, "oracle_bot", fake)
    monkeypatch.setattr(guard.economics, "active", lambda: True)
    monkeypatch.setattr(guard, "_open_position_accumulation_allows", lambda symbol: (True, "no_open_position"))
    monkeypatch.setattr(
        guard.economics,
        "fee_edge_allows_entry",
        lambda signal: (True, "edge_clears_negative_economics_buffer", 0.90, 0.55),
    )
    monkeypatch.setattr(
        guard.economics,
        "adjusted_optimizer_target",
        lambda signal, target: (10.0, scorecard, "research_tier_exploration_cap"),
    )
    monkeypatch.setattr(guard.economics, "log_economics", lambda *args, **kwargs: None)
    monkeypatch.setattr(guard.regime_gate, "regime_validation_ok", lambda signal: (True, "pass"))

    previous = guard._INSTALLED
    guard._INSTALLED = False
    try:
        assert guard.install_paper_strategy_execution_guard() is True
        signal = {
            "symbol": "AAVE-USD",
            "action": "BUY",
            "v39_optimizer_approved_amount": 20.0,
        }
        first = fake._buy(
            "crypto",
            "AAVE-USD",
            155.0,
            signal,
            verified_quote={"quote_timestamp": "2026-09-28T16:00:00Z"},
        )
        second = fake._buy(
            "crypto",
            "AAVE-USD",
            155.1,
            signal,
            verified_quote={"quote_timestamp": "2026-09-28T16:05:00Z"},
        )

        assert first == (False, "research_entry_confirmation_pending:1/2", None)
        assert second == (True, "original_buy", None)
        assert len(calls) == 1
    finally:
        guard._INSTALLED = previous
        guard._RESEARCH_ENTRY_CONFIRMATIONS.clear()



def test_zero_tier_capacity_clears_stale_research_confirmation(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")
    guard._RESEARCH_ENTRY_CONFIRMATIONS.clear()
    guard._RESEARCH_ENTRY_CONFIRMATIONS["SOL-USD"] = {
        "observation_id": "stale-open-position-scan",
        "seen_at": datetime.now(timezone.utc),
        "action": "BUY",
        "edge": 0.90,
        "cost": 0.55,
    }

    calls = []

    def original_buy(*args, **kwargs):
        calls.append((args, kwargs))
        return True, "original_buy", None

    fake = SimpleNamespace(_buy=original_buy, row=lambda *args, **kwargs: None)
    scorecard = SimpleNamespace(
        size_multiplier=0.35,
        model_tier="RESEARCH",
        sample_count=102,
        expectancy=-0.21,
        profit_factor=0.19,
    )
    monkeypatch.setitem(sys.modules, "oracle_bot", fake)
    monkeypatch.setattr(guard.economics, "active", lambda: True)
    monkeypatch.setattr(
        guard,
        "_open_position_accumulation_allows",
        lambda symbol: (True, "open_position_accumulation_cooldown_elapsed:6.00/5.00m"),
    )
    monkeypatch.setattr(
        guard.economics,
        "fee_edge_allows_entry",
        lambda signal: (True, "edge_clears_negative_economics_buffer", 0.90, 0.55),
    )
    monkeypatch.setattr(
        guard.economics,
        "adjusted_optimizer_target",
        lambda signal, target: (10.0, scorecard, "research_tier_exploration_cap"),
    )
    monkeypatch.setattr(
        guard,
        "_remaining_cumulative_tier_capacity",
        lambda symbol, target: (0.0, 10.0),
    )
    monkeypatch.setattr(guard.economics, "log_economics", lambda *args, **kwargs: None)
    monkeypatch.setattr(guard.regime_gate, "regime_validation_ok", lambda signal: (True, "pass"))

    previous = guard._INSTALLED
    guard._INSTALLED = False
    try:
        assert guard.install_paper_strategy_execution_guard() is True
        result = fake._buy(
            "crypto",
            "SOL-USD",
            118.0,
            {
                "symbol": "SOL-USD",
                "action": "BUY",
                "v39_optimizer_approved_amount": 20.0,
            },
            verified_quote={"quote_timestamp": "2026-09-28T21:00:00Z"},
        )

        assert result == (False, "strategy_economics_zero_target", None)
        assert "SOL-USD" not in guard._RESEARCH_ENTRY_CONFIRMATIONS
        assert calls == []
    finally:
        guard._INSTALLED = previous
        guard._RESEARCH_ENTRY_CONFIRMATIONS.clear()
