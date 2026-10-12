"""Regression tests for paper-only, episode-disjoint winner research."""
from datetime import datetime, timezone

from paper_winner_episode_research import chronological_episode_holdout, qualified_trade

ENTRY = "2026-10-01T12:00:00+00:00"
EXIT = "2026-10-01T13:00:00+00:00"
LATER_ENTRY = "2026-10-10T12:00:00+00:00"
LATER_EXIT = "2026-10-10T13:00:00+00:00"
CUTOFF = "2026-10-05T00:00:00+00:00"


def trade(episode, *, later=False, pnl=1.0, phenotype="breakout"):
    return {"entry_time": LATER_ENTRY if later else ENTRY,
            "exit_time": LATER_EXIT if later else EXIT,
            "feature_observed_at": LATER_ENTRY if later else ENTRY,
            "episode_id": episode, "entry_signal_id": "123",
            "entry_forecast_id": "abc", "entry_phenotype": phenotype,
            "fill_reconciled": True, "fees_verified": True,
            "net_pnl_after_fees": pnl, "symbol": "BTC-USD"}


def test_missing_evidence_and_lookahead_fail_closed():
    valid = trade("e1")
    assert qualified_trade(valid)
    for key, value in (("fees_verified", False),
                       ("fill_reconciled", False),
                       ("entry_forecast_id", None),
                       ("feature_observed_at", "2026-10-02T00:00:00+00:00"),
                       ("net_pnl_after_fees", float("nan"))):
        altered = {**valid, key: value}
        assert qualified_trade(altered) is None


def test_overlapping_episodes_are_excluded_from_both_sides():
    rows = [trade("shared"), trade("shared", later=True),
            trade("train"), trade("test", later=True)]
    result = chronological_episode_holdout(rows, cutoff=CUTOFF,
                                           min_training_episodes=1,
                                           min_holdout_episodes=1)
    item = result["phenotypes"]["breakout"]
    assert result["excluded_unqualified_or_leaking"] == 2
    assert item["training"]["episodes"] == 1
    assert item["holdout"]["episodes"] == 1


def test_winners_only_never_establish_profit_factor_or_promotion():
    rows = [trade("train"), trade("test", later=True)]
    result = chronological_episode_holdout(rows, cutoff=CUTOFF,
                                           min_training_episodes=1,
                                           min_holdout_episodes=1)
    item = result["phenotypes"]["breakout"]
    assert item["training"]["profit_factor_defined"] is False
    assert item["training_candidate"] is False
    assert item["promotion_authorized"] is False


def test_losses_and_holdout_failures_prevent_positive_validation():
    rows = [trade("a", pnl=3), trade("b", pnl=-1),
            trade("c", later=True, pnl=1), trade("d", later=True, pnl=-4)]
    result = chronological_episode_holdout(rows, cutoff=CUTOFF,
                                           min_training_episodes=2,
                                           min_holdout_episodes=2)
    item = result["phenotypes"]["breakout"]
    assert item["training_candidate"] is True
    assert item["holdout_positive"] is False
    assert result["execution_impact"] == "NONE"


def test_positive_holdout_still_does_not_authorize_promotion():
    rows = [trade("a", pnl=3), trade("b", pnl=-1),
            trade("c", later=True, pnl=3), trade("d", later=True, pnl=-1)]
    item = chronological_episode_holdout(rows, cutoff=CUTOFF,
                                        min_training_episodes=2,
                                        min_holdout_episodes=2)["phenotypes"]["breakout"]
    assert item["holdout_positive"] is True
    assert item["promotion_authorized"] is False
