"""Regression-first: boolean financial values cannot certify Kelly shadow evidence.

These tests use synthetic, fully completed paper outcomes. They do not create
trades, access production, or change Kelly thresholds.
"""
from datetime import datetime, timedelta, timezone

import pytest

from paper_kelly_evidence_shadow import CompletedPaperOutcome, evaluate_prior_generation_evidence
from paper_kelly_sizing_shadow import propose_kelly_size


_BASE = {
    "win_probability": 0.75,
    "lower_bound_win_probability": 0.65,
    "average_net_win_pct": 2.0,
    "average_net_loss_pct": -1.0,
    "independent_episodes": 30,
    "completed_trades": 60,
    "fraction_of_kelly": 0.25,
    "max_allocation": 0.10,
}


@pytest.mark.parametrize(
    "field",
    [
        "win_probability",
        "lower_bound_win_probability",
        "average_net_win_pct",
        "average_net_loss_pct",
        "fraction_of_kelly",
        "max_allocation",
    ],
)
@pytest.mark.parametrize("boolean", [True, False])
def test_boolean_kelly_numeric_input_is_not_evidence(field, boolean):
    arguments = {**_BASE, field: boolean}
    result = propose_kelly_size(**arguments)
    assert result.eligible is False
    assert result.reason == "invalid_numeric_input"
    assert result.full_fraction == 0.0
    assert result.proposed_fraction == 0.0


@pytest.mark.parametrize("boolean", [True, False])
def test_boolean_completed_paper_return_is_not_winner_or_flat_trade(boolean):
    candidate = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
    outcomes = [
        CompletedPaperOutcome(
            trade_id=f"paper-{index}",
            generation_id="generation-1",
            episode_id=f"episode-{index // 2}",
            entry_time=candidate - timedelta(days=31 - index // 2, hours=2),
            exit_time=candidate - timedelta(days=31 - index // 2, hours=1),
            net_return_pct=2.0 if index % 4 else -1.0,
        )
        for index in range(60)
    ]
    outcomes[0] = CompletedPaperOutcome(
        trade_id=outcomes[0].trade_id,
        generation_id=outcomes[0].generation_id,
        episode_id=outcomes[0].episode_id,
        entry_time=outcomes[0].entry_time,
        exit_time=outcomes[0].exit_time,
        net_return_pct=boolean,
    )
    result = evaluate_prior_generation_evidence(
        outcomes=outcomes,
        generation_id="generation-1",
        candidate_entry_time=candidate,
        lower_bound_win_probability=0.65,
        candidate_episode_id="new-episode",
    )
    assert result.eligible is False
    assert result.reason == "invalid_net_return"
    assert result.proposed_fraction == 0.0


def test_valid_numeric_kelly_controls_remain_eligible():
    result = propose_kelly_size(**_BASE)
    assert result.eligible is True
    assert result.reason == "shadow_only"
    assert 0 < result.proposed_fraction <= 0.10


@pytest.mark.parametrize("field", [
    "round_trip_net_pnl", "quantity", "entry_price", "round_trip_fees",
])
@pytest.mark.parametrize("boolean", [True, False])
def test_boolean_raw_fill_cost_or_quantity_cannot_become_kelly_return(field, boolean):
    from paper_kelly_challenger_shadow import _net_return_pct

    row = {
        "round_trip_net_pnl": 1.0,
        "quantity": 1.0,
        "entry_price": 100.0,
        "round_trip_fees": 0.10,
        "cost_provenance": "exact_lot",
    }
    row[field] = boolean
    assert _net_return_pct(row) is None


@pytest.mark.parametrize("boolean", [True, False])
def test_boolean_episode_return_cannot_count_as_calibrated_win(boolean):
    from paper_kelly_challenger_shadow import episode_win_lower_bound

    entry = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
    row = CompletedPaperOutcome(
        "paper-bool", "generation-1", "episode-1",
        entry, entry + timedelta(minutes=5), boolean,
    )
    assert episode_win_lower_bound([row]) is None
