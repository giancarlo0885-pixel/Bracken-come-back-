"""Kelly V1: forward-only evidence and immutable Council benchmark regression."""
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path

import pytest

from paper_kelly_challenger_shadow import (
    _episode, _net_return_pct, active, episode_win_lower_bound,
    evaluate_paper_candidate,
)
from paper_kelly_evidence_shadow import CompletedPaperOutcome


NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
GEN_STARTED = NOW - timedelta(days=120)


def make_trade(index, *, outcome=2.0, generation_started_at=None):
    entry = NOW - timedelta(days=index + 1)
    return {
        "trade_id": str(index),
        "generation": 1, "config_hash": "verified-config-hash",
        "generation_started_at": generation_started_at or GEN_STARTED,
        "entry_time": entry,
        "exit_time": entry + timedelta(hours=1),
        "quantity": 1.0,
        "entry_price": 100.0,
        "round_trip_net_pnl": outcome,
        "round_trip_fees": .10,
        "cost_provenance": "exact_lot",
    }


def test_episode_utc_calendar_day():
    assert _episode(datetime(2026, 10, 8, 19, tzinfo=timezone(timedelta(hours=-7)))) == "2026-10-09"


def test_exact_lot_net_return_percentage():
    assert _net_return_pct(make_trade(1, outcome=2)) == pytest.approx(2)
    assert _net_return_pct({**make_trade(1), "cost_provenance":"legacy_unknown"}) is None
    assert _net_return_pct({**make_trade(1), "entry_price":0}) is None
    assert _net_return_pct({**make_trade(1), "round_trip_fees":float("nan")}) is None


def test_complete_forward_candidate_uses_prior_episode_evidence():
    candidate = make_trade(0, outcome=2.5)
    # 60 wins, 20 losses, each from independent UTC dates.
    prior = [make_trade(i, outcome=(1.5 if i % 4 else -1.0)) for i in range(2, 82)]
    # Prior close in the candidate UTC episode: deliberately extreme, excluded.
    intraday = make_trade(0, outcome=-100)
    intraday["trade_id"] = "earlier_same_day"
    intraday["entry_time"] = NOW - timedelta(hours=5)
    intraday["exit_time"] = NOW - timedelta(hours=4)
    # Candidate must enter after that close for this to be a valid prior observation.
    candidate["entry_time"] = NOW
    candidate["exit_time"] = NOW + timedelta(hours=2)
    proposal, diag = evaluate_paper_candidate(candidate=candidate, prior_rows=prior + [intraday])
    assert proposal.eligible
    assert 0 < proposal.proposed_fraction <= .10
    assert diag["prior_trades"] == 80
    assert diag["prior_episodes"] == 80
    assert diag["council_net_return_pct"] == 2.5
    assert diag["win_probability"] == pytest.approx(.75)
    assert 0 < diag["win_lower_bound"] < diag["win_probability"]


def test_no_lookahead_and_no_cross_generation():
    candidate = make_trade(0)
    candidate["entry_time"] = NOW
    candidate["exit_time"] = NOW + timedelta(hours=2)
    prior = [make_trade(2)]
    future = {**prior[0], "trade_id": "future", "entry_time": NOW+timedelta(hours=1),
              "exit_time": NOW+timedelta(hours=2)}
    result, _ = evaluate_paper_candidate(candidate=candidate, prior_rows=prior + [future])
    assert result.reason == "invalid_prior_provenance"
    wrong_generation = {**prior[0], "entry_time": GEN_STARTED-timedelta(hours=1),
                        "exit_time": GEN_STARTED-timedelta(minutes=30)}
    result, _ = evaluate_paper_candidate(candidate=candidate, prior_rows=[wrong_generation])
    assert result.reason == "invalid_prior_provenance"


def test_negative_edge_and_insufficient_episodes_abstain():
    candidate = make_trade(0)
    candidate["entry_time"] = NOW
    candidate["exit_time"] = NOW + timedelta(hours=2)
    few = [make_trade(i, outcome=2.0 if i % 4 else -1) for i in range(2, 15)]
    proposal, _ = evaluate_paper_candidate(candidate=candidate, prior_rows=few)
    assert not proposal.eligible
    assert proposal.proposed_fraction == 0
    mostly_losses = [make_trade(i, outcome=1.0 if i % 4 == 0 else -2.0) for i in range(2,82)]
    proposal, diag = evaluate_paper_candidate(candidate=candidate, prior_rows=mostly_losses)
    assert proposal.reason == "nonpositive_conservative_edge"
    assert proposal.proposed_fraction == 0
    assert diag["prior_episodes"] == 80


def test_invalid_paper_provenance_abstains():
    candidate = make_trade(0)
    candidate["entry_time"] = NOW
    candidate["exit_time"] = NOW + timedelta(hours=2)
    result, _ = evaluate_paper_candidate(candidate=candidate, prior_rows=[
        {**make_trade(2), "round_trip_fees":None}])
    assert result.reason == "incomplete_prior_cost_provenance"
    result, _ = evaluate_paper_candidate(candidate={
        **candidate, "cost_provenance":"legacy_unknown"}, prior_rows=[])
    assert result.reason == "missing_exact_lot_return"


def test_duplicate_id_abstains():
    candidate = make_trade(0)
    candidate["entry_time"] = NOW
    candidate["exit_time"] = NOW + timedelta(hours=2)
    prior = make_trade(2)
    proposal, _ = evaluate_paper_candidate(candidate=candidate, prior_rows=[prior, prior])
    assert proposal.reason == "invalid_prior_provenance"


def test_episode_confidence_is_conservative():
    items = []
    for i in range(25):
        t = NOW - timedelta(days=i + 1)
        for j in range(20):
            items.append(CompletedPaperOutcome(f"{i}-{j}", "g1", str(i), t, t+timedelta(minutes=5),
                                               1.0 if j < 15 else -1.0))
    lower = episode_win_lower_bound(items)
    assert 0.0 <= lower < .75
    # More trades on a single day must not falsely create more independent episodes.
    assert lower == pytest.approx(.75 - math.sqrt(math.log(20)/(2*25)))


def test_runtime_disarms_when_live_or_broker_enabled(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")
    assert active()
    monkeypatch.setenv("LIVE_TRADING_ARMED", "true")
    assert not active()
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "true")
    assert not active()
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("EXECUTION_MODE", "live")
    assert not active()


def test_entrypoint_is_observation_only():
    source = Path("crypto_worker_entrypoint.py").read_text()
    assert "install_aeve_generation_controller()" in source
    assert "install_paper_kelly_shadow()" in source
    assert source.index("install_aeve_generation_controller()") < source.index("install_paper_kelly_shadow()")
    shadow = Path("paper_kelly_challenger_shadow.py").read_text()
    assert "paper_kelly_shadow_epoch" in shadow
    assert "m.entry_time >= %s" in shadow
    assert "m.exit_time < %s" in shadow
    assert "m.cost_provenance='exact_lot'" in shadow
    assert "broker.submit" not in shadow.lower()
