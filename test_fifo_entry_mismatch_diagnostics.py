"""Regression-first: distinguish FIFO entry mismatch causes without relaxing eligibility.

Each reason counts affected SELL closes, not every matched member or log repeat.
All cases use persisted synthetic paper fill evidence; no production writes.
"""
from datetime import timedelta

import pytest

from paper_exit_research import reconcile_fifo
from test_paper_exit_research import START, buy, sell


REASONS = (
    "fifo_entry_price_mismatch",
    "fifo_entry_time_mismatch",
    "fifo_entry_provenance_id_mismatch",
    "fifo_entry_canonical_lot_mismatch",
)


def _report(opening, closing):
    return reconcile_fifo([opening, closing], as_of=START + timedelta(hours=1))


@pytest.mark.parametrize(
    "change, expected_reason",
    [
        ({"entry_price": 99}, "fifo_entry_price_mismatch"),
        ({"entry_time": START + timedelta(minutes=1)}, "fifo_entry_time_mismatch"),
        ({"entry_signal_id": "different-signal"}, "fifo_entry_provenance_id_mismatch"),
        ({"_canonical_lot_id": "different-lot"}, "fifo_entry_canonical_lot_mismatch"),
    ],
)
def test_fifo_mismatch_reasons_preserve_rejection(change, expected_reason):
    opening = buy("buy", 2, 100, 0.2)
    closing = sell("sell", 2, 100, 101, 0.1, 2)
    if expected_reason == "fifo_entry_provenance_id_mismatch":
        opening["entry_signal_id"] = "original-signal"
    if expected_reason == "fifo_entry_canonical_lot_mismatch":
        opening["_canonical_lot_id"] = "original-lot"
    closing.update(change)

    report = _report(opening, closing)
    diagnostics = report["diagnostics"]
    assert report["qualified_fifo_closes"] == 0
    assert report["closes"][0]["entry_identity_matches"] is False
    assert report["closes"][0]["net_pnl"] is None
    assert diagnostics["fifo_entry_identity_mismatch"] == 1
    assert diagnostics[expected_reason] == 1
    assert sum(diagnostics.get(reason, 0) for reason in REASONS) == 1


def test_multiple_fifo_reasons_count_once_per_sell_not_per_member():
    opening = {**buy("buy", 2, 100, 0.2),
               "entry_signal_id": "original", "_canonical_lot_id": "lot-original"}
    closing = {**sell("sell", 2, 99, 101, 0.1, 2, entry_minute=1),
               "entry_signal_id": "different", "_canonical_lot_id": "lot-other"}
    report = _report(opening, closing)
    assert report["qualified_fifo_closes"] == 0
    assert report["diagnostics"]["fifo_entry_identity_mismatch"] == 1
    assert all(report["diagnostics"][reason] == 1 for reason in REASONS)


def test_matching_entry_has_no_mismatch_reasons_or_changed_economics():
    report = _report(buy("buy", 2, 100, 0.2),
                     sell("sell", 2, 100, 101, 0.1, 2))
    assert report["qualified_fifo_closes"] == 1
    assert report["net_pnl"] == pytest.approx(1.7)
    assert report["diagnostics"].get("fifo_entry_identity_mismatch", 0) == 0
    assert all(report["diagnostics"].get(reason, 0) == 0 for reason in REASONS)
    assert report["execution_impact"] == "NONE"


def test_multi_buy_lot_sell_is_distinguished_from_scalar_entry_mismatches():
    first = buy("buy-first", 1, 100, 0.1)
    second = buy("buy-second", 1, 110, 0.1, minute=1)
    closing = sell("sell", 2, 100, 120, 0.2, 30)
    report = reconcile_fifo([first, second, closing],
                            as_of=START + timedelta(hours=1))
    assert len(report["closes"][0]["members"]) == 2
    assert report["qualified_fifo_closes"] == 0
    assert report["diagnostics"]["fifo_entry_identity_mismatch"] == 1
    assert report["diagnostics"]["fifo_entry_multiple_buy_lots"] == 1
    assert report["closes"][0]["net_pnl"] is None
