"""AEVE shadow entry excursion evidence must be finite, signed and nonboolean.

Synthetic fixtures only. These tests do not authorize paper or live execution.
"""

import pytest

from paper_aeve_v1_formula import score_entry


def candidate(**changes):
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
    values.update(changes)
    return values


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("mfe_pct", True),
        ("mfe_pct", None),
        ("mfe_pct", float("nan")),
        ("mfe_pct", float("inf")),
        ("mfe_pct", -0.1),
        ("mae_pct", True),
        ("mae_pct", None),
        ("mae_pct", float("nan")),
        ("mae_pct", float("-inf")),
        ("mae_pct", "invalid"),
        ("mae_pct", 0.10),
    ],
)
def test_malformed_entry_excursions_cannot_qualify_shadow_candidate(field, bad_value):
    assert score_entry(**candidate(**{field: bad_value})).would_trade is False


def test_legitimate_signed_excursions_and_existing_entry_gate_are_preserved():
    assert score_entry(**candidate()).would_trade is True
    assert score_entry(**candidate(mae_pct=0.0)).would_trade is True
    assert score_entry(**candidate(mfe_pct=0.0)).would_trade is False
    assert score_entry(**candidate(mae_pct=-2.0)).would_trade is False
