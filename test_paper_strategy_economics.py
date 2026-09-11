from __future__ import annotations

from types import SimpleNamespace

import paper_strategy_economics as econ


def _paper(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")


def test_active_is_fail_closed_for_live(monkeypatch):
    _paper(monkeypatch)
    assert econ.active() is True
    monkeypatch.setenv("LIVE_TRADING_ARMED", "true")
    assert econ.active() is False


def test_fee_edge_blocks_explicit_edge_below_cost(monkeypatch):
    _paper(monkeypatch)
    monkeypatch.setenv("PAPER_MIN_EDGE_TO_COST_MULTIPLIER", "1.25")
    signal = {
        "expected_return_pct": 0.40,
        "expected_slippage_pct": 0.165,
        "fee_pct": 0.10,
        "spread_pct": 0.02,
    }
    allowed, reason, edge, cost = econ.fee_edge_allows_entry(signal)
    assert allowed is False
    assert edge == 0.40
    assert cost > 0.50
    assert reason.startswith("edge_below_round_trip_cost")


def test_fee_edge_preserves_exploration_when_edge_missing(monkeypatch):
    _paper(monkeypatch)
    allowed, reason, edge, cost = econ.fee_edge_allows_entry({"strategy": "explore"})
    assert allowed is True
    assert reason == "edge_unavailable_exploration"
    assert edge is None
    assert cost > 0


def test_negative_expectancy_reduces_size_but_keeps_exploration(monkeypatch):
    _paper(monkeypatch)
    monkeypatch.setenv("PAPER_STRATEGY_ECON_MIN_SAMPLES", "8")
    monkeypatch.setenv("PAPER_STRATEGY_EXPLORATION_FLOOR", "0.35")
    multiplier = econ._multiplier(
        sample_count=20,
        expectancy=-0.25,
        profit_factor=0.50,
        model_validated=False,
    )
    assert 0.35 <= multiplier < 1.0


def test_positive_size_boost_requires_model_validation(monkeypatch):
    _paper(monkeypatch)
    monkeypatch.setenv("PAPER_STRATEGY_MAX_SIZE_MULTIPLIER", "1.25")
    assert econ._multiplier(
        sample_count=40,
        expectancy=0.15,
        profit_factor=1.40,
        model_validated=False,
    ) == 1.0
    assert econ._multiplier(
        sample_count=40,
        expectancy=0.15,
        profit_factor=1.40,
        model_validated=True,
    ) == 1.25


def test_strategy_identity_prefers_explicit_strategy():
    signal = SimpleNamespace(strategy="momentum_v2", reason="generic")
    assert econ.strategy_identity(signal) == "momentum_v2"
