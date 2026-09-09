from __future__ import annotations

from datetime import datetime, timezone

from paper_sell_db_verification import _rolling_match_summary


def _trade(trade_id: int, symbol: str, qty: float, stamp: str) -> dict:
    return {"id": trade_id, "symbol": symbol, "quantity": qty, "created_at": stamp}


def _order(order_id: str, symbol: str, qty: float, stamp: str) -> dict:
    return {
        "order_id": order_id,
        "symbol": symbol,
        "status": "FILLED",
        "filled_quantity": qty,
        "created_at": stamp,
    }


def _fill(order_id: str, symbol: str, qty: float, stamp: str) -> dict:
    return {
        "fill_id": f"fill:{order_id}",
        "order_id": order_id,
        "symbol": symbol,
        "quantity": qty,
        "created_at": stamp,
    }


def test_rolling_sell_summary_requires_unique_order_and_fill_per_trade():
    trades = [
        _trade(1, "BTC-USD", 0.01, "2026-09-09T10:00:00+00:00"),
        _trade(2, "BTC-USD", 0.02, "2026-09-09T10:01:00+00:00"),
    ]
    orders = [
        _order("o1", "BTC-USD", 0.01, "2026-09-09T10:00:01+00:00"),
        _order("o2", "BTC-USD", 0.02, "2026-09-09T10:01:01+00:00"),
    ]
    fills = [
        _fill("o1", "BTC-USD", 0.01, "2026-09-09T10:00:01+00:00"),
        _fill("o2", "BTC-USD", 0.02, "2026-09-09T10:01:01+00:00"),
    ]

    result = _rolling_match_summary(trades, orders, fills)

    assert result["ok"] is True
    assert result["matched"] == 2
    assert result["sampled"] == 2
    assert result["failures"] == []


def test_rolling_sell_summary_fails_when_fill_quantity_does_not_match():
    trades = [_trade(1, "ETH-USD", 1.25, "2026-09-09T10:00:00+00:00")]
    orders = [_order("o1", "ETH-USD", 1.25, "2026-09-09T10:00:01+00:00")]
    fills = [_fill("o1", "ETH-USD", 1.20, "2026-09-09T10:00:01+00:00")]

    result = _rolling_match_summary(trades, orders, fills)

    assert result["ok"] is False
    assert result["matched"] == 0
    assert result["failures"][0]["reason"] == "matching_fill_missing_or_quantity_mismatch"


def test_rolling_sell_summary_does_not_reuse_one_order_for_two_trades():
    trades = [
        _trade(1, "SOL-USD", 2.0, "2026-09-09T10:00:00+00:00"),
        _trade(2, "SOL-USD", 2.0, "2026-09-09T10:00:02+00:00"),
    ]
    orders = [_order("o1", "SOL-USD", 2.0, "2026-09-09T10:00:01+00:00")]
    fills = [_fill("o1", "SOL-USD", 2.0, "2026-09-09T10:00:01+00:00")]

    result = _rolling_match_summary(trades, orders, fills)

    assert result["ok"] is False
    assert result["matched"] == 1
    assert len(result["failures"]) == 1
