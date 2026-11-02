"""Fail-first: forward exit research must not consume future outcomes."""
from datetime import datetime, timedelta, timezone

import pytest

from paper_exit_research import chronological_oos_report

CUTOFF = datetime(2026, 10, 11, tzinfo=timezone.utc)


def experiment(*, entry, trigger, exit_):
    return {
        "episode_id": "future-outcome-regression",
        "entry_time": entry,
        "exit_time": exit_,
        "trigger_snapshot": {"challenger_trigger_at": trigger},
        "challenger_exit_type": "FORWARD_EVIDENCE_EXIT",
        "actual_realized_r_net": -0.4,
        "challenger_counterfactual_r_net": -0.1,
        "entry_pattern": "dip",
        "regime": "range",
    }


@pytest.mark.parametrize("future_part", ["exit", "entry_and_exit"])
def test_future_dated_outcome_never_counts_as_avoided_loss(future_part):
    now = datetime.now(timezone.utc)
    if future_part == "exit":
        entry = CUTOFF + timedelta(days=2)
        trigger = entry + timedelta(minutes=10)
        exit_ = now + timedelta(days=1)
    else:
        entry = now + timedelta(hours=1)
        trigger = entry + timedelta(minutes=10)
        exit_ = entry + timedelta(minutes=20)
    report = chronological_oos_report([experiment(entry=entry, trigger=trigger, exit_=exit_)], CUTOFF)
    assert report["exclusions"]["future_or_open_outcome"] == 1
    assert report["avoided_loss_candidates"] == 0
    assert report["harmed_winner_candidates"] == 0
    assert report["promotion_action"] == "NONE"


def test_completed_forward_outcome_still_evaluated():
    entry = CUTOFF + timedelta(days=2)
    report = chronological_oos_report([experiment(
        entry=entry, trigger=entry + timedelta(minutes=10),
        exit_=entry + timedelta(minutes=20))], CUTOFF)
    assert report["exclusions"].get("future_or_open_outcome", 0) == 0
    assert report["avoided_loss_candidates"] == 1
    assert report["promotion_action"] == "NONE"
