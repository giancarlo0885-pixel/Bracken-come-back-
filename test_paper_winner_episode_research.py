"""Regression tests for paper-only, episode-disjoint winner research."""
from datetime import datetime, timezone

from paper_winner_episode_research import chronological_episode_holdout, qualified_trade

ENTRY = "2026-10-01T12:00:00+00:00"
EXIT = "2026-10-01T13:00:00+00:00"
LATER_ENTRY = "2026-10-10T12:00:00+00:00"
LATER_EXIT = "2026-10-10T13:00:00+00:00"
CUTOFF = "2026-10-05T00:00:00+00:00"


def trade(episode, *, later=False, pnl=1.0, phenotype="breakout"):
    return {"trade_id": f"{episode}:{later}", "entry_time": LATER_ENTRY if later else ENTRY,
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


def test_episode_risk_report_realized_loss_and_tail():
    from paper_winner_episode_research import episode_risk_report
    rows = [trade("one", pnl=3), trade("two", later=True, pnl=-5)]
    result = episode_risk_report(rows, confidence=0.5)
    assert result["episodes"] == 2
    assert result["max_closed_episode_drawdown"] == 5
    assert result["var_loss"] == 5
    assert result["cvar_loss"] == 5
    assert result["promotion_authorized"] is False


def test_matched_exits_reject_future_quotes_and_missing_pairs():
    from paper_winner_episode_research import matched_exit_comparison
    base = {"matched_forward_observation": True, "fees_verified": True,
            "decision_time": ENTRY, "quote_observed_at": ENTRY,
            "champion_net_pnl": -2, "challenger_net_pnl": 1,
            "episode_id": "episode1", "trade_id": "trade1"}
    result = matched_exit_comparison([
        base,
        {**base, "trade_id": "future", "quote_observed_at": EXIT},
        {**base, "trade_id": "missing", "matched_forward_observation": False},
    ])
    assert result["matched_pairs"] == 1
    assert result["unqualified_pairs"] == 2
    assert result["avoided_losses"] == 1
    assert result["missed_winners"] == 0
    assert result["promotion_authorized"] is False


def test_matched_exits_deduplicate_canonical_trade_identity():
    from paper_winner_episode_research import matched_exit_comparison
    row = {"matched_forward_observation": True, "fees_verified": True,
           "decision_time": ENTRY, "quote_observed_at": ENTRY,
           "champion_net_pnl": -2, "challenger_net_pnl": 1,
           "episode_id": "episode1", "trade_id": "trade1"}
    result = matched_exit_comparison([row, {**row, "challenger_net_pnl": 1000}])
    assert result["matched_pairs"] == 1
    assert result["unqualified_pairs"] == 1
    assert result["average_net_delta"] == 3


def test_holdout_duplicate_canonical_trade_rejected():
    row = trade("original")
    result = chronological_episode_holdout([row, {**row, "episode_id": "duplicate"}],
                                           cutoff=CUTOFF, min_training_episodes=1)
    assert result["excluded_unqualified_or_leaking"] == 1
    assert result["phenotypes"]["breakout"]["training"]["trades"] == 1


def test_episode_risk_deduplicates_canonical_trade_id():
    from paper_winner_episode_research import episode_risk_report
    row = trade("one", pnl=-3)
    result = episode_risk_report([row, {**row, "net_pnl_after_fees": -300}])
    assert result["qualified_trades"] == 1
    assert result["episodes"] == 1
    assert result["max_closed_episode_drawdown"] == 3
