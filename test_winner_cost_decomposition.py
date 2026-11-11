"""Regression-first: fee drag must be attributable on each verified entry lot.

These tests use the canonical FIFO reconciler; no trades or database records
are created, and outcome classification must remain unchanged.
"""
from paper_exit_research import reconcile_fifo
from paper_winner_memory import winner_entry_memory
from test_paper_winner_memory import history, exact_close


def _memory(rows):
    return winner_entry_memory(reconcile_fifo(rows, require_fill_fee_evidence=True)["closes"])


def test_verified_winner_reports_gross_and_both_original_fee_legs():
    result = _memory(history())
    assert (result["winners"], result["losers"]) == (1, 0)
    record = result["records"][0]
    assert record["cost_decomposition_status"] == "VERIFIED"
    assert record["gross_pnl"] == 4.0
    assert record["entry_fees"] == 0.2
    assert record["exit_fees"] == 0.1
    assert record["fee_drag"] == 0.3
    assert record["net_pnl"] == 3.7
    assert record["fee_erased_gross_win"] is False


def test_positive_gross_outcome_erased_by_fees_remains_a_loser():
    rows = history()
    rows[1].update(exit_price=100.1, gross_pnl=0.2, net_pnl=0.1)
    result = _memory(rows)
    assert (result["winners"], result["losers"]) == (0, 1)
    record = result["records"][0]
    assert record["cost_decomposition_status"] == "VERIFIED"
    assert abs(record["gross_pnl"] - 0.2) < 1e-10
    assert record["fee_drag"] == 0.3
    assert abs(record["net_pnl"] + 0.1) < 1e-10
    assert record["fee_erased_gross_win"] is True


def test_missing_cost_components_are_not_silently_fabricated():
    result = winner_entry_memory([exact_close()])
    record = result["records"][0]
    assert result["winners"] == 1
    assert record["cost_decomposition_status"] == "UNAVAILABLE"
    assert record["gross_pnl"] is None
    assert record["entry_fees"] is None
    assert record["exit_fees"] is None
    assert record["fee_drag"] is None
    assert record["fee_erased_gross_win"] is None
