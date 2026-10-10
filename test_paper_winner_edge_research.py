from datetime import datetime, timedelta, timezone

import pytest

from paper_winner_edge_research import discover, prepare

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
CUTOFF = START + timedelta(days=31)
AS_OF = CUTOFF + timedelta(days=40)


def trade(i, pnl=1.0, *, days=None):
    entry = START + timedelta(days=i // 2 if days is None else days)
    return dict(trade_id=str(i), market="crypto", strategy="council", regime="trend_up",
                entry_pattern="resistance_test", entry_time=entry.isoformat(),
                exit_time=(entry + timedelta(minutes=30)).isoformat(),
                entry_evidence_time=entry.isoformat(), entry_notional=100,
                net_pnl=pnl, fifo_complete=True, fill_fee_evidence_verified=True,
                entry_cohort_verified=True)


def report(rows):
    return discover(rows, cutoff=CUTOFF, as_of=AS_OF)


def test_repeatable_winner_cohort_selected_and_forward_scored_separately():
    rows = [trade(i) for i in range(60)]
    result = report(rows + [trade(100, 2, days=32)])
    assert result["candidates"][0]["research_candidate"] is True
    assert result["forward"]["selected"]["net_pnl"] == 2
    assert result["promotion_action"] == "NONE"


def test_small_winner_pocket_and_correlated_wins_never_qualify():
    assert not report([trade(i) for i in range(9)])["candidates"][0]["research_candidate"]
    assert not report([trade(i, days=0) for i in range(100)])["candidates"][0]["research_candidate"]


def test_winner_only_success_rate_cannot_hide_large_losses():
    rows = [trade(i, 1 if i % 2 == 0 else -20) for i in range(60)]
    candidate = report(rows)["candidates"][0]
    assert candidate["mean_net_return"] < 0
    assert not candidate["research_candidate"]


def test_forward_outcomes_cannot_change_discovery():
    rows = [trade(i) for i in range(60)]
    assert report(rows + [trade(100, -1000, days=32)])["candidates"] == report(rows)["candidates"]


def test_rejected_winners_and_avoided_losses_both_reported():
    rows = [trade(i) for i in range(60)]
    forward = [{**trade(100, 4, days=32), "strategy": "new"},
               {**trade(101, -3, days=33), "strategy": "new"}]
    result = report(rows + forward)["forward"]
    assert result["rejected_winner_pnl"] == 4
    assert result["avoided_loss_pnl"] == 3
    assert result["selection_pnl_delta"] == -1


def test_missing_posthoc_future_duplicate_and_conflicting_evidence():
    row = trade(0)
    future = trade(1, days=100)
    posthoc = {**trade(2), "entry_evidence_time": (START + timedelta(days=20)).isoformat()}
    unverified = {**trade(3), "fill_fee_evidence_verified": False}
    valid, excluded = prepare([row, row, future, posthoc, unverified], AS_OF)
    assert len(valid) == 1
    assert excluded["duplicate"] == 1
    assert excluded["future_or_open_outcome"] == 1
    assert excluded["invalid_entry_time_provenance"] == 1
    assert excluded["unverified_accounting_or_cohort"] == 1
    assert not prepare([row, {**row, "net_pnl": 2}], AS_OF)[0]


def test_split_episode_and_timezone_validation():
    cutoff = CUTOFF + timedelta(hours=1)
    result = discover([trade(1, days=31)], cutoff=cutoff, as_of=AS_OF)
    assert result["exclusions"]["boundary_or_open_training_episode"] == 1
    with pytest.raises(ValueError):
        discover([], cutoff=CUTOFF.replace(tzinfo=None), as_of=AS_OF)


def test_adapter_uses_allocated_notional_and_buy_snapshot_only():
    from paper_winner_edge_research import verified_ledger_inputs
    from test_paper_exit_research import buy, sell, START as EVENT
    opening = {**buy("buy", 2, 100, 0.2),
               "decision_timestamp": EVENT.isoformat(),
               "feature_snapshot": {"regime": "trend_up", "entry_pattern": "dip_rebound"}}
    closing = {**sell("sell", 2, 100, 102, 0.1, 4),
               "strategy": "posthoc", "feature_snapshot": {"regime": "posthoc"}}
    fills = [dict(fill_id="b", market="crypto", symbol="TEST-USD", side="BUY",
                  quantity=2, fill_price=100, fee_amount=0.2, created_at=EVENT),
             dict(fill_id="s", market="crypto", symbol="TEST-USD", side="SELL",
                  quantity=2, fill_price=102, fee_amount=0.1,
                  created_at=EVENT + timedelta(minutes=20))]
    rows, coverage = verified_ledger_inputs([opening, closing], fills, as_of=EVENT + timedelta(days=1))
    assert coverage["exported_trades"] == 1
    assert rows[0]["strategy"] == "council"
    assert rows[0]["entry_pattern"] == "dip_rebound"
    assert rows[0]["entry_notional"] == 200
    assert rows[0]["net_pnl"] == pytest.approx(3.7)
    opening["decision_timestamp"] = (EVENT + timedelta(minutes=1)).isoformat()
    rows, coverage = verified_ledger_inputs([opening, closing], fills, as_of=EVENT + timedelta(days=1))
    assert not rows
    assert coverage["adapter"]["unverified_or_mixed_entry_snapshot"] == 1


def test_nonfinite_normalized_return_is_excluded():
    row = {**trade(1), "net_pnl": 1e308, "entry_notional": 1e-308}
    rows, exclusions = prepare([row], AS_OF)
    assert not rows
    assert exclusions["nonfinite_normalized_return"] == 1


def test_partial_exits_count_as_one_completed_entry():
    from paper_winner_edge_research import verified_ledger_inputs
    from test_paper_exit_research import buy, sell, START as EVENT
    opening = {**buy("buy", 2, 100, 0.2), "decision_timestamp": EVENT.isoformat(),
               "feature_snapshot": {"regime": "trend_up", "entry_pattern": "dip_rebound"}}
    closes = [sell("s1", 1, 100, 102, 0.1, 2),
              sell("s2", 1, 100, 103, 0.1, 3, minute=30)]
    fills = [dict(fill_id="b", market="crypto", symbol="TEST-USD", side="BUY",
                  quantity=2, fill_price=100, fee_amount=0.2, created_at=EVENT)]
    for i, price in enumerate((102, 103)):
        fills.append(dict(fill_id=f"s{i}", market="crypto", symbol="TEST-USD", side="SELL",
                          quantity=1, fill_price=price, fee_amount=0.1,
                          created_at=EVENT + timedelta(minutes=20 + 10*i)))
    rows, _ = verified_ledger_inputs([opening, *closes], fills, as_of=EVENT + timedelta(days=1))
    assert len(rows) == 1
    assert rows[0]["trade_id"] == "buy"
    assert rows[0]["net_pnl"] == pytest.approx(4.6)
    assert rows[0]["entry_notional"] == 200
    assert not verified_ledger_inputs([opening, closes[0]], fills, as_of=EVENT + timedelta(days=1))[0]
