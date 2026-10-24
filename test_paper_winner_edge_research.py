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
                exact_fill_lot_linkage_verified=True, entry_cohort_verified=True)


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


@pytest.mark.parametrize("future_first", [False, True])
def test_future_conflicting_outcome_cannot_change_historical_discovery(future_first):
    rows = [trade(i) for i in range(60)]
    future = {**trade(0, -1000, days=100), "strategy": "future_strategy"}
    history = [future, *rows] if future_first else [*rows, future]
    observed = report(history)
    assert observed["candidates"] == report(rows)["candidates"]
    assert observed["exclusions"]["future_or_open_outcome"] == 1
    assert "missing_or_conflicting_identity" not in observed["exclusions"]


def test_split_episode_and_timezone_validation():
    cutoff = CUTOFF + timedelta(hours=1)
    result = discover([trade(1, days=31)], cutoff=cutoff, as_of=AS_OF)
    assert result["exclusions"]["boundary_or_open_training_episode"] == 1
    with pytest.raises(ValueError):
        discover([], cutoff=CUTOFF.replace(tzinfo=None), as_of=AS_OF)


def test_adapter_uses_allocated_notional_and_buy_snapshot_only():
    from paper_winner_edge_research import verified_ledger_inputs
    from test_paper_exact_fill_research import exact_allocations
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
    ledger = [opening, closing]
    rows, coverage = verified_ledger_inputs(ledger, fills, allocations=exact_allocations(ledger, {"sell": "buy"}),
                                           as_of=EVENT + timedelta(days=1))
    assert coverage["exported_trades"] == 1
    assert rows[0]["strategy"] == "council"
    assert rows[0]["entry_pattern"] == "dip_rebound"
    assert rows[0]["entry_notional"] == 200
    assert rows[0]["net_pnl"] == pytest.approx(3.7)
    opening["decision_timestamp"] = (EVENT + timedelta(minutes=1)).isoformat()
    rows, coverage = verified_ledger_inputs(ledger, fills, allocations=exact_allocations(ledger, {"sell": "buy"}),
                                           as_of=EVENT + timedelta(days=1))
    assert not rows
    assert coverage["adapter"]["unverified_or_mixed_entry_snapshot"] == 1


def test_nonfinite_normalized_return_is_excluded():
    row = {**trade(1), "net_pnl": 1e308, "entry_notional": 1e-308}
    rows, exclusions = prepare([row], AS_OF)
    assert not rows
    assert exclusions["nonfinite_normalized_return"] == 1


def test_partial_exits_count_as_one_completed_entry():
    from paper_winner_edge_research import verified_ledger_inputs
    from test_paper_exact_fill_research import exact_allocations
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
    ledger = [opening, *closes]
    links = exact_allocations(ledger, {"s1": "buy", "s2": "buy"})
    rows, _ = verified_ledger_inputs(ledger, fills, allocations=links, as_of=EVENT + timedelta(days=1))
    assert len(rows) == 1
    assert rows[0]["trade_id"] == "buy"
    assert rows[0]["net_pnl"] == pytest.approx(4.6)
    assert rows[0]["entry_fees"] == pytest.approx(.2)
    assert rows[0]["exit_fees"] == pytest.approx(.2)
    assert rows[0]["gross_pnl"] == pytest.approx(5)
    assert rows[0]["entry_notional"] == 200
    assert not verified_ledger_inputs([opening, closes[0]], fills, allocations=links, as_of=EVENT + timedelta(days=1))[0]
    from paper_economic_diagnosis import diagnose_economics
    diagnosis = diagnose_economics(rows, market="crypto", as_of=EVENT+timedelta(days=1))
    assert diagnosis["qualified_entry_lots"] == 1
    assert diagnosis["accounting"]["explicit_fees"] == pytest.approx(.4)
    assert diagnosis["accounting"]["gross_fill_price_pnl"] == pytest.approx(5)
    assert diagnosis["accounting"]["net_pnl"] == pytest.approx(4.6)


@pytest.mark.parametrize("missing_flag", [False, True])
def test_every_partial_close_requires_verified_fill_fees(missing_flag):
    from paper_exit_research import attach_fill_fee_evidence, reconcile_fifo
    from paper_winner_edge_research import verified_ledger_inputs
    from test_paper_exit_research import buy, sell, START as EVENT
    from test_paper_exact_fill_research import exact_allocations

    opening = {**buy("buy", 2, 100, 0.2), "decision_timestamp": EVENT.isoformat(),
               "feature_snapshot": {"regime": "trend_up", "entry_pattern": "dip_rebound"}}
    ledger = [opening, sell("s1", 1, 100, 102, 0.1, 2),
              sell("s2", 1, 100, 103, 0.1, 3, minute=30)]
    fills = [dict(fill_id="b", market="crypto", symbol="TEST-USD", side="BUY",
                  quantity=2, fill_price=100, fee_amount=0.2, created_at=EVENT),
             dict(fill_id="s1", market="crypto", symbol="TEST-USD", side="SELL",
                  quantity=1, fill_price=102, fee_amount=0.1,
                  created_at=EVENT + timedelta(minutes=20))]
    as_of = EVENT + timedelta(days=1)
    # A reused FIFO snapshot may still carry an old aggregate complete flag
    # after an individual allocation's fee proof becomes unavailable.
    links = exact_allocations(ledger, {"s1": "buy", "s2": "buy"})
    snapshot = reconcile_fifo(attach_fill_fee_evidence(ledger, fills, links), as_of=as_of)
    assert all(close["fifo_complete"] for close in snapshot["closes"])
    assert snapshot["closes"][0]["fill_fee_evidence_verified"] is True
    snapshot["closes"][1]["fill_fee_evidence_verified"] = False
    assert snapshot["closes"][1]["fill_fee_evidence_verified"] is False
    if missing_flag:
        snapshot["closes"][1].pop("fill_fee_evidence_verified")
    rows, coverage = verified_ledger_inputs(ledger, fills, as_of=as_of, fifo_report=snapshot)
    assert not rows
    assert coverage["adapter"]["unverified_canonical_fill_fees"] == 1


@pytest.mark.parametrize("future_first", [False, True])
def test_future_buy_correction_cannot_relabel_historical_entry(future_first):
    from paper_winner_edge_research import verified_ledger_inputs
    from test_paper_exact_fill_research import exact_allocations
    from test_paper_exit_research import buy, sell, START as EVENT

    opening = {**buy("buy", 2, 100, 0.2), "decision_timestamp": EVENT.isoformat(),
               "feature_snapshot": {"regime": "trend_up", "entry_pattern": "dip_rebound"}}
    closing = sell("sell", 2, 100, 102, 0.1, 4)
    fills = [dict(fill_id="b", market="crypto", symbol="TEST-USD", side="BUY",
                  quantity=2, fill_price=100, fee_amount=0.2, created_at=EVENT),
             dict(fill_id="s", market="crypto", symbol="TEST-USD", side="SELL",
                  quantity=2, fill_price=102, fee_amount=0.1,
                  created_at=EVENT + timedelta(minutes=20))]
    as_of = EVENT + timedelta(hours=1)
    links = exact_allocations([opening, closing], {"sell": "buy"})
    baseline, _ = verified_ledger_inputs([opening, closing], fills, allocations=links, as_of=as_of)
    correction = {**opening, "entry_time": EVENT + timedelta(hours=2),
                  "entry_price": 999, "strategy": "future_strategy",
                  "feature_snapshot": {"regime": "future_regime", "entry_pattern": "future_pattern"}}
    ledger = [correction, opening, closing] if future_first else [opening, closing, correction]
    rows, coverage = verified_ledger_inputs(ledger, fills, allocations=links, as_of=as_of)
    assert rows == baseline
    assert rows[0]["entry_notional"] == 200
    assert coverage["fifo"]["future_fill"] == 1


def test_runtime_freezes_discovery_before_later_outcomes_or_history_changes():
    from paper_winner_edge_research import runtime_report, FORWARD_CUTOFF
    rows = [trade(i) for i in range(60)]
    before = runtime_report(rows, as_of=FORWARD_CUTOFF - timedelta(hours=1))
    assert before["phase"] == "training_only"
    frozen = runtime_report(rows, as_of=FORWARD_CUTOFF + timedelta(hours=1), previous=before)
    assert frozen["discovery_frozen"]
    days = (FORWARD_CUTOFF - START).days + 1
    changed = [{**r, "net_pnl": -100} for r in rows]
    later = runtime_report(changed + [trade(100, 2, days=days)],
                           as_of=FORWARD_CUTOFF + timedelta(days=2), previous=frozen)
    assert later["candidates"] == frozen["candidates"]
    assert later["forward"]["selected"]["net_pnl"] == 2
    assert later["promotion_action"] == "NONE"


@pytest.mark.parametrize("market", ["cash", "crypto"])
def test_frozen_runtime_preserves_boundary_episode_purge(monkeypatch, market):
    import paper_winner_edge_research as research

    cutoff = CUTOFF + timedelta(hours=1)
    monkeypatch.setattr(research, "FORWARD_CUTOFF", cutoff)
    training = [{**trade(i), "market": market} for i in range(60)]
    boundary_entry = cutoff + timedelta(minutes=1)
    boundary = {**trade(100, 100, days=31), "market": market,
                "entry_time": boundary_entry.isoformat(),
                "entry_evidence_time": boundary_entry.isoformat(),
                "exit_time": (boundary_entry + timedelta(minutes=30)).isoformat()}
    forward = {**trade(101, 2, days=32), "market": market}
    frozen = research.runtime_report(training + [boundary, forward], as_of=AS_OF)
    repeated = research.runtime_report(training + [boundary, forward],
                                       as_of=AS_OF, previous=frozen)
    assert frozen["discovery_frozen"] is True
    assert frozen["candidates"][0]["research_candidate"] is True
    assert repeated["forward"] == frozen["forward"]
    assert repeated["forward"]["baseline"]["trades"] == 1
    assert repeated["forward"]["selected"]["net_pnl"] == 2
    assert repeated["exclusions"]["boundary_or_open_training_episode"] == 1


def test_runtime_reuses_reconciled_snapshot_and_classifies_observed_entry_features():
    from paper_winner_edge_research import verified_ledger_inputs
    opening = {"trade_id": "b", "side": "BUY", "quantity": 2, "entry_price": 100,
               "strategy": "council", "decision_timestamp": START.isoformat(),
               "feature_snapshot": {"trend_strength": 0.1, "volatility_20d": 0.7,
                                    "schwager_pattern_tag": "resistance_test"}}
    close = dict(trade_id="s", market="crypto", entry_time=START.isoformat(),
                 exit_time=(START+timedelta(hours=1)).isoformat(), fifo_complete=True,
                 fill_fee_evidence_verified=True, exact_fill_lot_linkage_verified=True, net_pnl=2,
                 exit_fill_id="sell-fill", members=[dict(buy_trade_id="b", quantity=2,
                 buy_fill_id="buy-fill", sell_fill_id="sell-fill", lot_id="lot:b")])
    snapshot = dict(closes=[close], diagnostics={}, observed_closes=1)
    rows, _ = verified_ledger_inputs([opening], [], as_of=AS_OF, fifo_report=snapshot)
    assert rows[0]["regime"] == "trend_up__high_vol"
    assert rows[0]["entry_pattern"] == "resistance_test"
    opening["feature_snapshot"].pop("trend_strength")
    assert not verified_ledger_inputs([opening], [], as_of=AS_OF, fifo_report=snapshot)[0]


def test_forward_diagnostics_report_post_cost_economics_and_counterfactual_scope(caplog):
    import json
    from paper_exit_research import emit_winner_edge_diagnostics
    from paper_winner_edge_research import runtime_report, FORWARD_CUTOFF

    days = (FORWARD_CUTOFF - START).days + 1
    # A small training pocket must not masquerade as an accepted strategy.
    rows = [trade(0)] + [trade(100, 4, days=days), trade(101, -3, days=days)]
    edge = runtime_report(rows, as_of=FORWARD_CUTOFF + timedelta(days=2),
                          coverage={"exported_trades": 3})
    edge["private_raw_rows"] = "must-not-be-logged"
    with caplog.at_level("INFO", logger="paper-exit-research"):
        emit_winner_edge_diagnostics("crypto", edge)
    line = next(r.message for r in caplog.records if "PAPER WINNER FORWARD ECONOMICS" in r.message)
    stats = json.loads(line.split("statistics=", 1)[1].split(" | scope=", 1)[0])
    assert stats["baseline"]["trades"] == 2
    assert stats["baseline"]["episodes"] == 1
    assert stats["baseline"]["net_pnl"] == 1
    assert stats["baseline"]["mean_net_return"] == pytest.approx(0.005)
    assert stats["baseline"]["score_lower_bound"] is None
    assert stats["selected"]["trades"] == 0
    assert stats["selected"]["mean_net_return"] is None
    assert stats["rejected_winner_pnl"] == 4
    assert stats["avoided_loss_pnl"] == 3
    assert "selection=COUNTERFACTUAL" in line
    assert "execution_impact=NONE" in line and "promotion_action=NONE" in line
    assert "must-not-be-logged" not in caplog.text


def test_legacy_fill_fee_flag_without_exact_lot_linkage_cannot_enter_discovery():
    row = trade(1)
    row.pop("exact_fill_lot_linkage_verified")
    valid, exclusions = prepare([row], AS_OF)
    assert valid == []
    assert exclusions["unverified_accounting_or_cohort"] == 1


@pytest.mark.parametrize("missing", ["lot_id", "buy_fill_id", "sell_fill_id", "exit_fill_id"])
def test_reused_fifo_snapshot_requires_explicit_identity_receipts_not_only_boolean_flag(missing):
    from paper_winner_edge_research import verified_ledger_inputs
    opening = {"trade_id": "b", "side": "BUY", "quantity": 2, "entry_price": 100,
               "strategy": "council", "decision_timestamp": START.isoformat(),
               "feature_snapshot": {"regime": "trend_up", "entry_pattern": "dip_rebound"}}
    close = dict(trade_id="s", market="crypto", entry_time=START.isoformat(),
                 exit_time=(START+timedelta(hours=1)).isoformat(), fifo_complete=True,
                 fill_fee_evidence_verified=True, exact_fill_lot_linkage_verified=True,
                 exit_fill_id="sell-fill", net_pnl=2,
                 members=[dict(buy_trade_id="b", quantity=2, lot_id="lot:b",
                               buy_fill_id="buy-fill", sell_fill_id="sell-fill")])
    if missing == "exit_fill_id":
        close.pop(missing)
    else:
        close["members"][0].pop(missing)
    rows, coverage = verified_ledger_inputs([opening], [], as_of=AS_OF,
        fifo_report=dict(closes=[close], diagnostics={}, observed_closes=1))
    assert rows == []
    assert coverage["adapter"]["unverified_exact_fill_lot_linkage"] == 1


def test_tiny_partly_closed_entry_is_not_a_completed_winner():
    from paper_winner_edge_research import verified_ledger_inputs
    from test_paper_exit_research import buy, sell, START as EVENT
    from test_paper_exact_fill_research import exact_allocations

    opening = {**buy("buy", 1e-13, 100, 0), "decision_timestamp": EVENT.isoformat(),
               "feature_snapshot": {"regime": "trend_up", "entry_pattern": "dip_rebound"}}
    ledger = [opening, sell("partial", 5e-14, 100, 102, 0, 1e-13)]
    rows, coverage = verified_ledger_inputs(ledger, [], as_of=EVENT+timedelta(days=1),
        allocations=exact_allocations(ledger, {"partial": "buy"}))
    assert rows == []
    assert coverage["adapter"]["open_or_partial_entry_lot"] == 1


def test_legacy_frozen_candidates_are_archived_without_reusing_or_reranking_holdout():
    import paper_winner_edge_research as research
    rows = [trade(i) for i in range(60)]
    legacy = research.runtime_report(rows, as_of=research.FORWARD_CUTOFF+timedelta(days=1))
    assert legacy["candidates"][0]["research_candidate"] is True
    legacy["version"] = "winner-edge-research-v1"
    legacy.pop("accounting_provenance")
    legacy_copy = dict(legacy)
    days = (research.FORWARD_CUTOFF-START).days+1
    current = research.runtime_report(rows+[trade(100, 2, days=days)],
        as_of=research.FORWARD_CUTOFF+timedelta(days=2), previous=legacy)
    assert current["candidates"] == []
    assert current["forward"]["baseline"]["net_pnl"] == 2
    assert current["forward"]["selected"]["net_pnl"] is None
    for field in ("avoided_loss_pnl", "rejected_winner_pnl", "rejected_winners", "selection_pnl_delta"):
        assert current["forward"][field] is None  # No validated frozen strategy exists for this comparison.
    assert current["historical_discovery_reference"]["candidates"] == legacy["candidates"]
    assert current["historical_discovery_reference"]["qualified_current_evidence"] is False
    assert current["cutoff"] == legacy["cutoff"]
    assert legacy == legacy_copy
    repeated = research.runtime_report(rows, as_of=research.FORWARD_CUTOFF+timedelta(days=3), previous=current)
    assert repeated["candidates"] == []
    assert repeated["research_blocker"] == current["research_blocker"]
    assert repeated["historical_discovery_reference"] == current["historical_discovery_reference"]
