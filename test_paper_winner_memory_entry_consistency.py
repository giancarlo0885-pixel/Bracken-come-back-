"""Regression: one original BUY fill cannot carry conflicting entry evidence across partial exits."""
from copy import deepcopy
from datetime import timedelta

import pytest

from paper_winner_memory import winner_entry_memory
from test_paper_winner_memory import START, exact_close


@pytest.mark.parametrize("field, changed", [
    ("entry_time", (START + timedelta(minutes=1)).isoformat()),
    ("decision_timestamp", (START - timedelta(minutes=1)).isoformat()),
    ("feature_snapshot", {"momentum_5d": -0.5}),
    ("entry_strategy", "posthoc_strategy"),
    ("entry_regime", "posthoc_regime"),
])
def test_partial_exits_with_conflicting_original_buy_snapshot_fail_closed(field, changed):
    first = exact_close(opened=2, quantity=1)
    second = exact_close(opened=2, quantity=1, sell_id="s2", sell_fill_id="sf2")
    second["members"][0][field] = changed
    result = winner_entry_memory([first, second])
    assert result["records"] == []
    assert result["winners"] == 0
    assert result["losers"] == 0
    assert result["exclusions"] == {"conflicting_entry_snapshot": 1}


def test_matching_partial_entry_snapshots_remain_one_completed_lot():
    first = exact_close(opened=2, quantity=1)
    second = deepcopy(exact_close(opened=2, quantity=1, sell_id="s2", sell_fill_id="sf2"))
    result = winner_entry_memory([first, second])
    assert len(result["records"]) == result["winners"] == 1
    assert result["exclusions"] == {}
