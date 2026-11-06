"""Regression-first: account for every verified winner and loser by entry cohort.

This is read-only diagnostic evidence. Cohorts are not strategy selections.
"""
import pytest

from paper_economic_diagnosis import diagnose_economics
from test_paper_economic_diagnosis import lot
from test_paper_winner_edge_research import AS_OF


def _diagnose(rows):
    return diagnose_economics(rows, market="crypto", as_of=AS_OF)


def test_entry_cohort_cost_attribution_includes_losers_and_fee_erased_gross_wins():
    rows = [
        lot(1, 4.0, 0.6, 0.4),   # +3.00 after costs
        lot(2, -2.0, 0.2, 0.3),  # -2.50 after costs
        {**lot(3, 1.0, 0.6, 0.6), "entry_pattern": "dip_rebound"},  # -0.20
    ]
    report = _diagnose(rows)
    groups = {item["entry_pattern"]: item for item in report["cohort_cost_attribution"]}
    assert set(groups) == {"resistance_test", "dip_rebound"}

    resistance = groups["resistance_test"]
    assert (resistance["qualified_entry_lots"], resistance["winners"],
            resistance["losers"], resistance["flat"]) == (2, 1, 1, 0)
    assert resistance["gross_fill_price_pnl"] == pytest.approx(2.0)
    assert resistance["explicit_fees"] == pytest.approx(1.5)
    assert resistance["net_pnl"] == pytest.approx(0.5)
    assert resistance["fee_drag_fraction_of_notional"] == pytest.approx(1.5 / 200)
    assert resistance["fee_erased_positive_gross_lots"] == 0

    dip = groups["dip_rebound"]
    assert (dip["winners"], dip["losers"]) == (0, 1)
    assert dip["gross_fill_price_pnl"] == pytest.approx(1.0)
    assert dip["explicit_fees"] == pytest.approx(1.2)
    assert dip["net_pnl"] == pytest.approx(-0.2)
    assert dip["fee_erased_positive_gross_lots"] == 1
    assert report["promotion_action"] == "NONE"


def test_cohort_attribution_reconciles_to_full_qualified_population_without_selection():
    rows = [
        lot(10, 3.0, 0.1, 0.1),
        {**lot(11, -3.0, 0.2, 0.2), "regime": "range__high_vol"},
        {**lot(12, -1.0, 0.3, 0.3), "entry_pattern": "dip_rebound"},
        {**lot(13, 0.4, 0.2, 0.2), "strategy": "another_strategy"},
    ]
    invalid = {**lot(14, 10.0), "fill_fee_evidence_verified": False}
    report = _diagnose([*rows, invalid])
    groups = report["cohort_cost_attribution"]

    assert report["qualified_entry_lots"] == 4
    assert sum(g["qualified_entry_lots"] for g in groups) == 4
    assert sum(g["winners"] for g in groups) == 1
    assert sum(g["losers"] for g in groups) == 2
    assert sum(g["flat"] for g in groups) == 1
    for group_field, accounting_field in (
        ("gross_fill_price_pnl", "gross_fill_price_pnl"),
        ("explicit_fees", "explicit_fees"),
        ("net_pnl", "net_pnl"),
        ("allocated_entry_notional", "allocated_entry_notional"),
    ):
        assert sum(g[group_field] for g in groups) == pytest.approx(
            report["accounting"][accounting_field]
        )
    assert all(group["promotion_action"] == "NONE" for group in groups)
    assert "descriptive" in report["cohort_attribution_semantics"].lower()


def test_no_qualified_lots_have_no_cohort_or_implied_profit():
    report = _diagnose([])
    assert report["cohort_cost_attribution"] == []
    assert report["accounting"]["net_pnl"] is None
    assert report["promotion_action"] == "NONE"
