"""Regression-first: reused FIFO snapshots must match original BUY entry time.

Research-only synthetic evidence; no trading, database or provider calls.
"""
from datetime import datetime, timedelta, timezone

from paper_winner_edge_research import verified_ledger_inputs


ENTRY = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)


def _buy(entry_time):
    return {
        "trade_id": "buy-1", "side": "BUY", "quantity": 2,
        "entry_price": 100, "strategy": "council",
        "entry_time": entry_time,
        "decision_timestamp": ENTRY.isoformat(),
        "feature_snapshot": {"regime": "trend_up", "entry_pattern": "dip_rebound"},
    }


def _close(entry_time):
    return {
        "trade_id": "sell-1", "market": "crypto", "symbol": "TEST-USD",
        "entry_time": entry_time,
        "exit_time": (ENTRY + timedelta(minutes=30)).isoformat(),
        "fifo_complete": True, "fill_fee_evidence_verified": True,
        "exact_fill_lot_linkage_verified": True,
        "exit_fill_id": "sell-fill", "net_pnl": 1.0,
        "members": [{
            "buy_trade_id": "buy-1", "buy_fill_id": "buy-fill",
            "sell_trade_id": "sell-1", "sell_fill_id": "sell-fill",
            "lot_id": "lot-1", "quantity": 2,
        }],
    }


def _research(buy_entry_time, close_entry_time):
    return verified_ledger_inputs(
        [_buy(buy_entry_time)], [],
        as_of=ENTRY + timedelta(hours=1),
        fifo_report={"closes": [_close(close_entry_time)],
                     "diagnostics": {}, "observed_closes": 1},
    )


def test_reused_snapshot_cannot_shift_original_buy_entry_time():
    rows, coverage = _research(
        ENTRY.isoformat(), (ENTRY + timedelta(minutes=15)).isoformat())
    assert rows == []
    assert coverage["adapter"]["mismatched_or_missing_buy_entry_time"] == 1


def test_reused_snapshot_cannot_supply_missing_original_buy_entry_time():
    rows, coverage = _research(None, ENTRY.isoformat())
    assert rows == []
    assert coverage["adapter"]["mismatched_or_missing_buy_entry_time"] == 1


def test_reused_snapshot_matching_original_buy_entry_time_is_accepted():
    rows, coverage = _research(ENTRY.isoformat(), ENTRY.isoformat())
    assert len(rows) == 1
    assert rows[0]["trade_id"] == "buy-1"
    assert rows[0]["entry_time"] == ENTRY.isoformat()
    assert coverage["adapter"].get("mismatched_or_missing_buy_entry_time", 0) == 0
