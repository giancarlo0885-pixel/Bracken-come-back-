"""Regression: reused FIFO snapshots cannot double-count a partial exit.

Research-only; no DB, execution or provider access.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from paper_winner_edge_research import verified_ledger_inputs

ENTRY = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
AS_OF = ENTRY + timedelta(days=1)


def _opening():
    return {
        "trade_id": "buy", "side": "BUY", "quantity": 2,
        "entry_price": 100, "strategy": "council",
        "entry_time": ENTRY.isoformat(),
        "decision_timestamp": ENTRY.isoformat(),
        "feature_snapshot": {"regime": "trend_up", "entry_pattern": "dip_rebound"},
    }


def _partial(trade_id="sell-1", fill_id="sell-fill-1", pnl=1):
    return {
        "trade_id": trade_id, "market": "crypto",
        "symbol": "TEST-USD", "entry_time": ENTRY.isoformat(),
        "exit_time": (ENTRY + timedelta(minutes=20)).isoformat(),
        "fifo_complete": True, "fill_fee_evidence_verified": True,
        "exact_fill_lot_linkage_verified": True,
        "exit_fill_id": fill_id, "net_pnl": pnl,
        "members": [{
            "buy_trade_id": "buy", "buy_fill_id": "buy-fill",
            "sell_trade_id": trade_id, "sell_fill_id": fill_id,
            "lot_id": "lot-buy", "quantity": 1,
        }],
    }


def _research(closes):
    return verified_ledger_inputs(
        [_opening()], [], as_of=AS_OF,
        fifo_report={"closes": closes, "diagnostics": {},
                     "observed_closes": len(closes)},
    )


def test_reused_duplicate_sell_cannot_manufacture_complete_profitable_lot():
    partial = _partial()
    rows, coverage = _research([partial, deepcopy(partial)])
    assert rows == []
    assert coverage["adapter"]["duplicate_exit_identity"] == 1


def test_two_sell_trade_ids_cannot_reuse_one_exit_fill_identity():
    first = _partial()
    second = _partial(trade_id="sell-2", fill_id="sell-fill-1", pnl=2)
    rows, coverage = _research([first, second])
    assert rows == []
    assert coverage["adapter"]["duplicate_exit_identity"] == 1


def test_distinct_verified_partial_exits_still_make_one_completed_lot():
    first = _partial()
    second = _partial(trade_id="sell-2", fill_id="sell-fill-2", pnl=2)
    rows, coverage = _research([first, second])
    assert coverage["adapter"].get("duplicate_exit_identity", 0) == 0
    assert len(rows) == 1
    assert rows[0]["trade_id"] == "buy"
    assert rows[0]["net_pnl"] == 3
    assert rows[0]["entry_notional"] == 200
    assert rows[0]["sell_trade_ids"] == ["sell-1", "sell-2"]
