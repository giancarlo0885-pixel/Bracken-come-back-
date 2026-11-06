"""AEVE V1 paper-only economics must not qualify malformed cost evidence."""

import math

import pytest

from paper_aeve_v1_formula import score_entry, should_take_profit


def candidate(**overrides):
    values = dict(
        expected_net_edge_pct=0.24,
        mfe_pct=1.10,
        mae_pct=-0.30,
        round_trip_cost_pct=0.15,
        loss_streak=0,
        dip_depth_pct=0.35,
        rebound_from_low_pct=0.45,
        rsi=44,
        trend_confirmed=True,
        regime_expectancy_positive=True,
        profit_factor=1.35,
        min_samples=80,
    )
    values.update(overrides)
    return values


@pytest.mark.parametrize("bad_cost", [None, False, -0.10, float("nan"), float("inf"), "invalid"])
def test_aeve_candidate_does_not_qualify_with_invalid_round_trip_cost(bad_cost):
    assert score_entry(**candidate(round_trip_cost_pct=bad_cost)).would_trade is False


@pytest.mark.parametrize("bad_cost", [None, False, -0.10, float("nan"), float("inf"), "invalid"])
def test_aeve_profit_exit_does_not_qualify_with_invalid_round_trip_cost(bad_cost):
    assert should_take_profit(
        unrealized_return_pct=0.85,
        round_trip_cost_pct=bad_cost,
        mfe_since_entry_pct=1.0,
        pullback_from_peak_pct=0.35,
    ) is False


def test_aeve_candidate_does_not_treat_boolean_edge_as_profit_evidence():
    assert score_entry(**candidate(expected_net_edge_pct=True)).would_trade is False


def test_aeve_valid_numeric_cost_controls_remain_unchanged():
    assert score_entry(**candidate()).would_trade is True
    assert score_entry(**candidate(round_trip_cost_pct=0.0)).would_trade is True
    assert score_entry(**candidate(round_trip_cost_pct=1.0)).would_trade is False
    assert should_take_profit(
        unrealized_return_pct=0.85,
        round_trip_cost_pct=0.15,
        mfe_since_entry_pct=1.0,
        pullback_from_peak_pct=0.35,
    ) is True
