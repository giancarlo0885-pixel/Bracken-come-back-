"""Regression-first: partial exits require chronological, as-of-safe evidence.

Synthetic read-only research snapshots; no market, database or execution calls.
"""
from datetime import datetime, timedelta, timezone

import pytest

from paper_winner_edge_research import verified_ledger_inputs


ENTRY = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
AS_OF = ENTRY + timedelta(hours=1)


def research(exit_times):
    buy = {
        "trade_id": "buy-1", "side": "BUY", "quantity": 2,
        "entry_price": 100, "strategy": "council",
        "entry_time": ENTRY.isoformat(),
        "decision_timestamp": ENTRY.isoformat(),
        "feature_snapshot": {"regime": "trend_up", "entry_pattern": "dip_rebound"},
    }
    closes = [
        {
            "trade_id": f"sell-{i}", "market": "crypto", "symbol": "TEST-USD",
            "entry_time": ENTRY.isoformat(), "exit_time": exit_time,
            "fifo_complete": True, "fill_fee_evidence_verified": True,
            "exact_fill_lot_linkage_verified": True,
            "exit_fill_id": f"sell-fill-{i}", "net_pnl": 0.5,
            "members": [{
                "buy_trade_id": "buy-1", "buy_fill_id": "buy-fill",
                "sell_trade_id": f"sell-{i}", "sell_fill_id": f"sell-fill-{i}",
                "lot_id": "lot-1", "quantity": 1,
            }],
        }
        for i, exit_time in enumerate(exit_times)
    ]
    return verified_ledger_inputs(
        [buy], [], as_of=AS_OF,
        fifo_report={"closes": closes, "diagnostics": {}, "observed_closes": 2},
    )


def test_latest_partial_exit_uses_absolute_time_not_lexical_offset_order():
    rows, coverage = research([
        (ENTRY + timedelta(minutes=30)).isoformat(),
        "2026-10-08T13:00:00+01:00",  # 12:00 UTC, earlier than first
    ])
    assert len(rows) == 1
    assert rows[0]["exit_time"] == (ENTRY + timedelta(minutes=30)).isoformat()
    assert coverage["adapter"].get("invalid_or_future_partial_exit_time", 0) == 0


@pytest.mark.parametrize("invalid_exit", [
    "2026-10-08T11:00:00-03:00",  # 14:00 UTC, future beyond AS_OF
    "",  # Missing timestamp must not be hidden by another partial exit
    "2026-10-08T11:30:00+00:00",  # Exit before original entry
])
def test_invalid_partial_exit_cannot_be_hidden_by_valid_later_string(invalid_exit):
    rows, coverage = research([
        (ENTRY + timedelta(minutes=30)).isoformat(),
        invalid_exit,
    ])
    assert rows == []
    assert coverage["adapter"]["invalid_or_future_partial_exit_time"] == 1


def test_valid_partial_exits_remain_eligible():
    rows, coverage = research([
        (ENTRY + timedelta(minutes=15)).isoformat(),
        (ENTRY + timedelta(minutes=45)).isoformat(),
    ])
    assert len(rows) == 1
    assert rows[0]["exit_time"] == (ENTRY + timedelta(minutes=45)).isoformat()
    assert rows[0]["net_pnl"] == 1.0
    assert coverage["adapter"].get("invalid_or_future_partial_exit_time", 0) == 0
