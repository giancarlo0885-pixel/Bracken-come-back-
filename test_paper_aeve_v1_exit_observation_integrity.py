"""AEVE shadow profit-exit observations must be finite, nonboolean measurements."""

import pytest

from paper_aeve_v1_formula import should_take_profit


def eligible(**overrides):
    evidence = dict(
        unrealized_return_pct=0.85,
        round_trip_cost_pct=0.15,
        mfe_since_entry_pct=1.0,
        pullback_from_peak_pct=0.35,
        minimum_net_profit_pct=0.20,
    )
    evidence.update(overrides)
    return should_take_profit(**evidence)


@pytest.mark.parametrize(
    "field,value",
    (
        ("unrealized_return_pct", True),
        ("mfe_since_entry_pct", True),
        ("mfe_since_entry_pct", None),
        ("mfe_since_entry_pct", -0.1),
        ("minimum_net_profit_pct", None),
        ("minimum_net_profit_pct", float("nan")),
        ("pullback_from_peak_pct", True),
        ("pullback_from_peak_pct", float("inf")),
    ),
)
def test_malformed_exit_observation_cannot_trigger_profit_claim(field, value):
    assert eligible(**{field: value}) is False


def test_valid_profit_exit_and_below_threshold_controls():
    assert eligible() is True
    assert eligible(unrealized_return_pct=0.1) is False
    assert eligible(pullback_from_peak_pct=0.1) is False
    assert eligible(minimum_net_profit_pct=0.8) is False
    assert eligible(round_trip_cost_pct=0.0) is True
