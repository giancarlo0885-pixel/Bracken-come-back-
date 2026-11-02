"""Regression: reused FIFO close snapshots cannot import exits after evaluation as_of.

Read-only research; synthetic ledger/fill evidence only.
"""
from datetime import datetime, timedelta, timezone

from paper_winner_edge_research import verified_ledger_inputs


ENTRY = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)


def _buy(quantity):
    return {
        "trade_id": "buy", "side": "BUY", "quantity": quantity,
        "entry_price": 100, "strategy": "council",
        "entry_time": ENTRY.isoformat(),
        "decision_timestamp": ENTRY.isoformat(),
        "feature_snapshot": {
            "regime": "trend_up", "entry_pattern": "dip_rebound",
        },
    }


def _close(sell, fill, quantity, minutes, pnl=1.0):
    return {
        "trade_id": sell, "market": "crypto", "symbol": "TEST-USD",
        "entry_time": ENTRY.isoformat(),
        "exit_time": (ENTRY + timedelta(minutes=minutes)).isoformat(),
        "fifo_complete": True, "fill_fee_evidence_verified": True,
        "exact_fill_lot_linkage_verified": True,
        "exit_fill_id": fill, "net_pnl": pnl,
        "members": [{
            "buy_trade_id": "buy", "buy_fill_id": "buy-fill",
            "sell_trade_id": sell, "sell_fill_id": fill,
            "lot_id": "lot-buy", "quantity": quantity,
        }],
    }


def _research(quantity, closes, minutes):
    return verified_ledger_inputs(
        [_buy(quantity)], [], as_of=ENTRY + timedelta(minutes=minutes),
        fifo_report={"closes": closes, "diagnostics": {},
                     "observed_closes": len(closes)},
    )


def test_reused_snapshot_future_full_exit_cannot_be_completed_at_asof():
    rows, coverage = _research(
        2, [_close("sell-1", "fill-1", 2, 20)], minutes=10,
    )
    assert rows == []
    assert coverage["adapter"]["future_or_unobserved_exit"] == 1


def test_reused_snapshot_future_partial_cannot_complete_lot_at_asof():
    rows, coverage = _research(
        2, [_close("sell-1", "fill-1", 1, 5),
            _close("sell-2", "fill-2", 1, 20)], minutes=10,
    )
    assert rows == []
    assert coverage["adapter"]["future_or_unobserved_exit"] == 1


def test_reused_snapshot_completed_distinct_exits_still_qualify():
    rows, coverage = _research(
        2, [_close("sell-1", "fill-1", 1, 5),
            _close("sell-2", "fill-2", 1, 20)], minutes=30,
    )
    assert len(rows) == 1
    assert rows[0]["trade_id"] == "buy"
    assert rows[0]["net_pnl"] == 2.0
    assert coverage["adapter"].get("future_or_unobserved_exit", 0) == 0
