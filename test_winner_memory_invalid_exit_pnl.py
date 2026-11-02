"""Fail-closed winner-memory evidence regressions; synthetic, no production writes."""
from datetime import timedelta

import pytest

from paper_winner_memory import winner_entry_memory
from test_paper_winner_memory import exact_close, START


@pytest.mark.parametrize("invalid_exit", [
    None,
    "not-a-timestamp",
    (START - timedelta(seconds=1)).isoformat(),
])
def test_winner_memory_rejects_missing_malformed_or_preentry_exit(invalid_exit):
    close = exact_close()
    close["exit_time"] = invalid_exit
    result = winner_entry_memory([close])
    assert result["records"] == []
    assert result["winners"] == result["losers"] == 0
    assert result["exclusions"] == {"invalid_exit_or_pnl_evidence": 1}


@pytest.mark.parametrize("invalid_pnl", [
    None,
    float("nan"),
    float("inf"),
    True,
    False,
])
def test_winner_memory_rejects_missing_nonfinite_or_boolean_pnl(invalid_pnl):
    close = exact_close()
    close["net_pnl"] = invalid_pnl
    result = winner_entry_memory([close])
    assert result["records"] == []
    assert result["winners"] == result["losers"] == 0
    assert result["exclusions"] == {"invalid_exit_or_pnl_evidence": 1}


def test_winner_memory_preserves_valid_winners_losers_and_partial_exits():
    win = exact_close()
    loss = exact_close(buy_id="b2", buy_fill_id="bf2", lot_id="lot-b2",
                       sell_id="s2", sell_fill_id="sf2")
    loss["net_pnl"] = -0.2
    result = winner_entry_memory([win, loss])
    assert len(result["records"]) == 2
    assert result["winners"] == result["losers"] == 1
    assert result["exclusions"] == {}
