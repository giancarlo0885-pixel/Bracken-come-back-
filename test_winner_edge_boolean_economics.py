"""Reject non-numeric boolean accounting values from winner-edge research.

These are synthetic research inputs, never executed fills or live orders.
"""
import pytest

from paper_winner_edge_research import prepare
from test_paper_winner_edge_research import AS_OF, trade


@pytest.mark.parametrize("field", ("net_pnl", "entry_notional"))
def test_boolean_accounting_cannot_create_a_qualified_winner(field):
    row = {**trade(0), field: True}
    qualified, exclusions = prepare([row], AS_OF)
    assert qualified == []
    assert exclusions["missing_features_or_economics"] == 1


def test_finite_numeric_accounting_still_qualifies():
    row = {**trade(0), "net_pnl": "1.25", "entry_notional": "100"}
    qualified, exclusions = prepare([row], AS_OF)
    assert exclusions == {}
    assert len(qualified) == 1
    assert qualified[0]["net_return"] == pytest.approx(0.0125)
