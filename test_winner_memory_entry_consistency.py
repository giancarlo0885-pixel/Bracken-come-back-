"""Synthetic regressions: partial exits must share one original entry fingerprint."""
from datetime import timedelta

import pytest

from paper_winner_memory import winner_entry_memory
from test_paper_winner_memory import exact_close, START


@pytest.mark.parametrize("field,replacement", [
    ("feature_snapshot", {"momentum_5d": -0.9}),
    ("decision_timestamp", (START - timedelta(seconds=1)).isoformat()),
    ("entry_time", (START + timedelta(seconds=1)).isoformat()),
    ("entry_strategy", "different-strategy"),
    ("entry_regime", "different-regime"),
])
def test_partial_closes_reject_conflicting_original_entry_evidence(field, replacement):
    first = exact_close(quantity=1)
    second = exact_close(quantity=1, sell_id="s2", sell_fill_id="sf2")
    second["members"][0][field] = replacement
    result = winner_entry_memory([first, second])
    assert result["records"] == []
    assert result["winners"] == result["losers"] == 0
    assert result["exclusions"] == {"conflicting_entry_evidence": 1}


def test_partial_closes_preserve_identical_original_entry_evidence():
    first = exact_close(quantity=1)
    second = exact_close(quantity=1, sell_id="s2", sell_fill_id="sf2")
    result = winner_entry_memory([first, second])
    assert len(result["records"]) == 1
    assert result["winners"] == 1
    assert result["exclusions"] == {}
