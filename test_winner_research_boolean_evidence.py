"""Regression-first winner-research numeric provenance checks; no execution path."""
import pytest

from paper_winner_edge_research import discover, prepare
from test_paper_winner_edge_research import AS_OF, CUTOFF, trade


@pytest.mark.parametrize(("field", "value"), [
    ("net_pnl", True), ("net_pnl", False),
    ("entry_notional", True),
])
def test_boolean_financial_evidence_never_qualifies_a_completed_lot(field, value):
    row = {**trade(1, 1.0), field: value}
    qualified, exclusions = prepare([row], AS_OF)
    assert qualified == []
    assert exclusions["missing_features_or_economics"] == 1


def test_boolean_winners_cannot_manufacture_a_profitable_training_cohort():
    # Sixty distinct IDs in thirty daily episodes would otherwise clear
    # the discovery sample/episode gates with a fabricated +1 return.
    rows = [trade(i, True) for i in range(60)]
    result = discover(rows, cutoff=CUTOFF, as_of=AS_OF)
    assert result["candidates"] == []
    assert result["exclusions"]["missing_features_or_economics"] == 60
    assert result["forward"]["selected"]["trades"] == 0
    assert result["promotion_action"] == "NONE"


def test_genuine_numeric_zero_and_one_remain_valid():
    rows = [trade(1, 0.0), trade(2, 1.0)]
    qualified, exclusions = prepare(rows, AS_OF)
    assert len(qualified) == 2
    assert [r["net_pnl"] for r in qualified] == [0.0, 1.0]
    assert exclusions.get("missing_features_or_economics", 0) == 0
