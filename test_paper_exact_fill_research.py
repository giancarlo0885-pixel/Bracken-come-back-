"""Exact persisted allocation fixtures and research provenance regressions."""
from copy import deepcopy
from datetime import timedelta

import pytest

from paper_exit_research import attach_fill_fee_evidence, reconcile_fifo
from paper_fill_provenance import PROVENANCE_VERSION
from test_paper_exit_research import START, buy, sell


def exact_allocations(ledger, sell_entries):
    """Build the actual nested loader contract using declared lot identities.

    A fixture supplies every SELL -> BUY relationship explicitly. No timestamp,
    price, quantity or nearby fill is used to choose an entry.
    """
    sources = {r["trade_id"]: deepcopy(r) for r in ledger}
    entries = {key: key for key, row in sources.items() if row["side"] == "BUY"}
    entries.update(sell_entries)
    evidence = {}
    for index, (identity, row) in enumerate(sources.items()):
        opening = sources[entries[identity]]
        lot_id = "lot:" + opening["trade_id"]
        fill_id, order_id = "fill:" + identity, "order:" + identity
        event = row["entry_time"] if row["side"] == "BUY" else row["exit_time"]
        price = row["entry_price"] if row["side"] == "BUY" else row["exit_price"]
        execution = dict(id=1000+index, market=row["market"], symbol=row["symbol"],
                         side=row["side"], quantity=row["quantity"], price=price,
                         value=price*row["quantity"], fees=row["fees"], created_at=event,
                         gross_realized_pnl=row["gross_pnl"],
                         realized_pnl=row["net_pnl"] if row["side"] == "SELL" else 0)
        fill = dict(fill_id=fill_id, order_id=order_id, market=row["market"], symbol=row["symbol"],
                    side=row["side"], canonical_trade_id=execution["id"], quantity=row["quantity"],
                    fill_price=price, notional=price*row["quantity"], fee_amount=row["fees"],
                    created_at=event+timedelta(seconds=1))
        order = dict(order_id=order_id, market=row["market"], symbol=row["symbol"], side=row["side"],
                     filled_quantity=row["quantity"], filled_notional=price*row["quantity"],
                     average_fill_price=price, fee_amount=row["fees"])
        lot = dict(lot_id=lot_id, market=row["market"], symbol=row["symbol"], broker_mode="PAPER",
                   account_environment="PAPER", entry_price=opening["entry_price"],
                   opened_at=opening["entry_time"], quantity_opened=opening["quantity"], entry_fees=opening["fees"])
        evidence[identity] = dict(trade_id=identity, lot_id=lot_id, fill_id=fill_id, side=row["side"],
                                  allocated_quantity=row["quantity"], allocated_fee=row["fees"],
                                  provenance_version=PROVENANCE_VERSION, fill=fill, paper_order=order,
                                  execution=execution, lot=lot, ledger=row,
                                  total_quantity=row["quantity"], total_fee=row["fees"], total_gross_pnl=row["gross_pnl"])
    for identity, row in evidence.items():
        entry = evidence[entries[identity]]
        row["lot_closed_quantity"] = sum(source["quantity"] for key, source in sources.items()
                                         if source["side"] == "SELL" and entries[key] == entries[identity])
        row.update(entry_allocation=dict(trade_id=entry["trade_id"], lot_id=entry["lot_id"],
                   fill_id=entry["fill_id"], side="BUY", quantity=entry["allocated_quantity"],
                   fee_amount=entry["allocated_fee"], provenance_version=PROVENANCE_VERSION),
                   entry_fill=deepcopy(entry["fill"]), entry_order=deepcopy(entry["paper_order"]),
                   entry_execution=deepcopy(entry["execution"]), entry_ledger=deepcopy(entry["ledger"]),
                   entry_total_quantity=entry["total_quantity"], entry_total_fee=entry["total_fee"],
                   entry_total_gross_pnl=entry["total_gross_pnl"])
    return list(evidence.values())


def test_unrelated_nearby_cheap_fill_cannot_turn_real_loss_into_winner():
    ledger = [buy("buy", 2, 100, .2), sell("sell", 2, 100, 100.2, .3, .4)]
    unrelated = [dict(fill_id="nearby-buy", market="crypto", symbol="TEST-USD", side="BUY",
                      quantity=2, fill_price=100, fee_amount=.2, created_at=START),
                 dict(fill_id="nearby-sell", market="crypto", symbol="TEST-USD", side="SELL",
                      quantity=2, fill_price=100.2, fee_amount=.1, created_at=START+timedelta(minutes=20))]
    unlinked = reconcile_fifo(attach_fill_fee_evidence(ledger, unrelated), require_fill_fee_evidence=True)
    assert unlinked["qualified_fifo_closes"] == 0
    assert unlinked["net_pnl"] is None
    factual = reconcile_fifo(attach_fill_fee_evidence(ledger, unrelated, exact_allocations(ledger, {"sell": "buy"})),
                             require_fill_fee_evidence=True)
    assert factual["qualified_fifo_closes"] == 1
    assert factual["net_pnl"] == pytest.approx(-.1)
    assert factual["closes"][0]["exact_fill_lot_linkage_verified"] is True


def test_stale_inferred_annotations_do_not_survive_no_explicit_links():
    ledger = [buy("buy", 2, 100, .2), sell("sell", 2, 100, 101, .1, 2)]
    tagged = attach_fill_fee_evidence(ledger, [], exact_allocations(ledger, {"sell": "buy"}))
    assert all("_canonical_fill_fee" in r for r in tagged)
    clean = attach_fill_fee_evidence(tagged, [])
    assert all(not any(key.startswith("_canonical_") for key in r) for r in clean)
    assert reconcile_fifo(clean, require_fill_fee_evidence=True)["net_pnl"] is None


@pytest.mark.parametrize("field", ["entry_signal_id", "entry_decision_id", "entry_forecast_id", "entry_quote_id"])
def test_matching_scalars_do_not_override_contradictory_entry_identity(field):
    ledger = [{**buy("buy", 2, 100, .2), field: "original"},
              {**sell("sell", 2, 100, 101, .1, 2), field: "different"}]
    backed = attach_fill_fee_evidence(ledger, [], exact_allocations(ledger, {"sell": "buy"}))
    report = reconcile_fifo(backed, require_fill_fee_evidence=True)
    assert report["qualified_fifo_closes"] == 0
    assert report["net_pnl"] is None


def test_duplicate_sources_and_allocations_never_charge_fees_twice():
    ledger = [buy("buy", 2, 100, .2), sell("sell", 2, 100, 101, .1, 2)]
    links = exact_allocations(ledger, {"sell": "buy"})
    report = reconcile_fifo(attach_fill_fee_evidence([*ledger, ledger[0]], [], [*links, links[0]]),
                            require_fill_fee_evidence=True)
    assert report["qualified_fifo_closes"] == 1
    assert report["net_pnl"] == pytest.approx(1.7)
    assert report["diagnostics"]["duplicate_fill"] == 1


def test_conflicting_explicit_allocations_fail_closed():
    ledger = [buy("buy", 2, 100, .2), sell("sell", 2, 100, 101, .1, 2)]
    links = exact_allocations(ledger, {"sell": "buy"})
    conflict = {**links[1], "lot_id": "different-lot"}
    report = reconcile_fifo(attach_fill_fee_evidence(ledger, [], [*links, conflict]), require_fill_fee_evidence=True)
    assert report["net_pnl"] is None


def test_future_fill_receipt_does_not_verify_historical_accounting():
    ledger = [buy("buy", 2, 100, .2), sell("sell", 2, 100, 101, .1, 2)]
    links = exact_allocations(ledger, {"sell": "buy"})
    as_of = START+timedelta(minutes=20)
    report = reconcile_fifo(attach_fill_fee_evidence(ledger, [], links, as_of=as_of), as_of=as_of,
                            require_fill_fee_evidence=True)
    assert report["net_pnl"] is None


def test_fifo_must_not_substitute_same_price_same_time_but_different_explicit_lot():
    ledger = [buy("first", 1, 100, .1), buy("second", 1, 100, .1),
              sell("sell", 1, 100, 101, .1, 1)]
    # Although the scalars coincide, this exit names the second lot. FIFO's
    # first-lot reconstruction cannot claim the explicit second-lot proof.
    links = exact_allocations(ledger, {"sell": "second"})
    report = reconcile_fifo(attach_fill_fee_evidence(ledger, [], links), require_fill_fee_evidence=True)
    assert report["qualified_fifo_closes"] == 0
    assert report["closes"][0]["entry_identity_matches"] is False
    assert report["closes"][0]["exact_fill_lot_linkage_verified"] is False


def test_matching_supplied_quote_ids_must_still_match_the_durable_canonical_rows():
    ledger = [{**buy("buy", 2, 100, .2), "entry_quote_id": "persisted-quote"},
              {**sell("sell", 2, 100, 101, .1, 2), "entry_quote_id": "persisted-quote"}]
    links = exact_allocations(ledger, {"sell": "buy"})
    supplied = [{**row, "entry_quote_id": "different-quote"} for row in ledger]
    report = reconcile_fifo(attach_fill_fee_evidence(supplied, [], links), require_fill_fee_evidence=True)
    assert report["qualified_fifo_closes"] == 0
    assert report["net_pnl"] is None


@pytest.mark.parametrize("changed", [{"regime": "posthoc_winner", "signal_flag": True},
                                    {"regime": "trend_up", "signal_flag": 1}])
def test_supplied_entry_features_must_match_persisted_snapshot_without_boolean_coercion(changed):
    original = {"regime": "trend_up", "signal_flag": True}
    ledger = [{**buy("buy", 2, 100, .2), "feature_snapshot": original},
              {**sell("sell", 2, 100, 101, .1, 2), "feature_snapshot": original}]
    links = exact_allocations(ledger, {"sell": "buy"})
    supplied = [{**row, "feature_snapshot": changed} for row in ledger]
    report = reconcile_fifo(attach_fill_fee_evidence(supplied, [], links), require_fill_fee_evidence=True)
    assert report["qualified_fifo_closes"] == 0
    assert report["net_pnl"] is None
