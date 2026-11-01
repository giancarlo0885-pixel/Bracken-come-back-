"""Fail-first regression for original SELL-fill identity in winner memory.

A fill may be allocated to different BUY lots, but must not count twice
toward closing the *same* BUY lot. Research only; no DB or broker access.
"""
from paper_winner_memory import winner_entry_memory
from test_paper_winner_memory import exact_close


def test_distinct_sell_trades_cannot_reuse_one_fill_to_manufacture_winner():
    first = exact_close(opened=2, quantity=1, sell_id="sell-a", sell_fill_id="fill-a")
    second = exact_close(opened=2, quantity=1, sell_id="sell-b", sell_fill_id="fill-a")
    result = winner_entry_memory([first, second])
    assert result["records"] == []
    assert result["winners"] == 0
    assert result["exclusions"] == {"unverified_fill_lot_linkage": 1}


def test_distinct_partial_sell_fills_remain_one_verified_winner():
    first = exact_close(opened=2, quantity=1, sell_id="sell-a", sell_fill_id="fill-a")
    second = exact_close(opened=2, quantity=1, sell_id="sell-b", sell_fill_id="fill-b")
    result = winner_entry_memory([first, second])
    assert result["winners"] == 1
    assert result["losers"] == 0
    assert len(result["records"]) == 1
    assert result["records"][0]["sell_trade_ids"] == ["sell-a", "sell-b"]
