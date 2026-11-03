"""Malformed realized P&L cannot manufacture profitable paper sizing evidence.

Synthetic fixtures only. No historical record mutation or trading authorization.
"""
import math

import paper_strategy_economics as econ


def _setup(monkeypatch, rows):
    monkeypatch.setattr(econ, "_CACHE", {})
    monkeypatch.setattr(econ, "_ledger_records", lambda strategy, symbol="": rows)
    monkeypatch.setattr(econ, "model_validation_ok", lambda signal: True)
    monkeypatch.setattr(econ, "paper_model_tier", lambda signal: "CAPITAL_QUALIFIED")
    monkeypatch.setenv("PAPER_STRATEGY_ECON_MIN_SAMPLES", "8")
    monkeypatch.setenv("PAPER_STRATEGY_MAX_SIZE_MULTIPLIER", "1.25")
    monkeypatch.setenv("PAPER_STRATEGY_EXPLORATION_FLOOR", "0.35")
    monkeypatch.setenv("PAPER_RESEARCH_MAX_SIZE_MULTIPLIER", "0.35")


def _row(pnl):
    return {"net_pnl": pnl, "gross_pnl": 1.1, "fees": 0.1,
            "entry_time": None, "exit_time": None}


def _score():
    return econ.strategy_economics(
        {"strategy": "synthetic", "symbol": "BTC-USD",
         "model": "approved", "model_version": "v1"}
    )


def test_boolean_net_pnl_cannot_become_a_profitable_trade(monkeypatch):
    _setup(monkeypatch, [_row(True) for _ in range(30)])
    result = _score()
    assert result.sample_count == 0
    assert result.net_pnl == 0.0
    assert result.model_validated is False
    assert result.model_tier == "RESEARCH"
    assert result.evidence_complete is False
    assert result.size_multiplier <= 1.0


def test_invalid_row_quarantines_capital_promotion_without_winner_only_bias(monkeypatch):
    _setup(monkeypatch, [_row(1.0) for _ in range(30)] + [_row(float("nan"))])
    result = _score()
    assert result.sample_count == 30
    assert math.isclose(result.net_pnl, 30.0)
    assert result.model_validated is False
    assert result.model_tier == "RESEARCH"
    assert result.size_multiplier <= 1.0


def test_finite_numeric_profit_preserves_existing_sizing(monkeypatch):
    _setup(monkeypatch, [_row(1.0) for _ in range(30)])
    result = _score()
    assert result.sample_count == 30
    assert result.net_pnl == 30.0
    assert result.model_validated is True
    assert result.model_tier == "CAPITAL_QUALIFIED"
    assert result.evidence_complete is True
    assert result.size_multiplier == 1.25


def test_incomplete_negative_cohort_cannot_reenable_missing_edge_exploration(monkeypatch):
    _setup(monkeypatch, [_row(-1.0) for _ in range(29)] + [_row(True)])
    monkeypatch.setenv("PAPER_KNOWN_NEGATIVE_EDGE_MIN_SAMPLES", "30")
    allowed, reason, edge, cost = econ.fee_edge_allows_entry(
        {"strategy": "synthetic", "symbol": "BTC-USD"}
    )
    assert allowed is False
    assert reason == "invalid_realized_pnl_evidence"
    assert edge is None and cost > 0


def test_incomplete_cohort_cannot_authorize_explicit_edge_either(monkeypatch):
    _setup(monkeypatch, [_row(-1.0) for _ in range(29)] + [_row(None)])
    allowed, reason, edge, cost = econ.fee_edge_allows_entry(
        {"strategy": "synthetic", "symbol": "BTC-USD",
         "expected_edge_pct": 2.0, "estimated_cost_pct": 0.5}
    )
    assert (allowed, reason, edge, cost) == (
        False, "invalid_realized_pnl_evidence", 2.0, 0.5
    )
