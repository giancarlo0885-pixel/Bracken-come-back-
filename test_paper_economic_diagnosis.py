"""Economic identities and evidence boundaries, not synthetic profit claims."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest

from paper_economic_diagnosis import diagnose_economics, fee_only_break_even_return
from test_paper_winner_edge_research import trade, AS_OF


def lot(identity, gross, entry_fee=.6, exit_fee=.6):
    return {**trade(identity, gross-entry_fee-exit_fee),
            "gross_pnl": gross, "entry_fees": entry_fee, "exit_fees": exit_fee}


def diagnose(rows):
    return diagnose_economics(rows, market="crypto", as_of=AS_OF)


def test_fee_only_break_even_includes_exit_fee_on_exit_notional():
    threshold = fee_only_break_even_return(.006, .006)
    assert threshold == pytest.approx(.0120724346076459)
    assert 100*(1+threshold)*(1-.006)-100*(1+.006) == pytest.approx(0, abs=1e-12)


@pytest.mark.parametrize("entry,exit_", [(None, .006), (.006, None),
    (-.1, .1), (.1, 1), (True, .1), (.1, float("nan")), ("1e999", .1)])
def test_missing_invalid_fees_never_produce_a_break_even_claim(entry, exit_):
    assert fee_only_break_even_return(entry, exit_) is None


def test_costs_erasing_positive_fill_return_are_distinguished_from_price_losses():
    report = diagnose([lot(1, 1)])
    assert report["status"] == "POSITIVE_FILL_PRICE_RETURN_ERASED_BY_FEES"
    assert report["accounting"]["gross_fill_price_pnl"] == 1
    assert report["accounting"]["explicit_fees"] == pytest.approx(1.2)
    assert report["accounting"]["net_pnl"] == pytest.approx(-.2)
    assert report["fee_erased_positive_gross_lots"] == 1
    loss = diagnose([lot(2, -1)])
    assert loss["status"] == "NONPOSITIVE_FILL_PRICE_ECONOMICS"
    assert loss["entry_vs_exit_cause"].startswith("UNRESOLVED")


def test_expectancy_uses_winners_losers_and_flats_without_double_cost_deduction():
    rows = [lot(1, 4), lot(2, -2), lot(3, 1.2)]
    result = diagnose(rows)
    expected = sum(r["net_pnl"] / r["entry_notional"] for r in rows) / 3
    assert result["expectancy"]["mean_net_return"] == pytest.approx(expected)
    assert result["expectancy"]["win_loss_decomposition"] == pytest.approx(expected)
    assert result["expectancy"]["flat_rate"] == pytest.approx(1/3)
    assert result["accounting"]["net_pnl"] == pytest.approx(-.4)
    assert result["accounting"]["gross_fill_price_pnl"]-result["accounting"]["explicit_fees"] == pytest.approx(-.4)
    assert result["promotion_action"] == "NONE" and result["execution_impact"] == "NONE"
    json.dumps(result, allow_nan=False)


def test_equal_lot_expectancy_is_not_confused_with_capital_weighted_return():
    a, b = lot(1, 11, 0, 1), lot(2, -99, 0, 1)
    b["entry_notional"] = 1000
    result = diagnose([a, b])
    assert result["expectancy"]["mean_net_return"] == pytest.approx(0)
    assert result["accounting"]["aggregate_return_on_allocated_notional"] == pytest.approx(-90/1100)


def test_bad_cost_identity_missing_provenance_and_future_data_do_not_qualify():
    corrupt = lot(1, 4); corrupt["net_pnl"] = 99
    incomplete = lot(2, 4); incomplete["exact_fill_lot_linkage_verified"] = False
    future = lot(3, 4); future["exit_time"] = (AS_OF+timedelta(days=1)).isoformat()
    missing = lot(4, 4); missing.pop("gross_pnl")
    other = lot(5, 4); other["market"] = "cash"
    result = diagnose([corrupt, incomplete, future, missing, other])
    assert result["qualified_entry_lots"] == 0
    assert result["accounting"]["net_pnl"] is None
    assert result["status"] == "INSUFFICIENT_QUALIFIED_EVIDENCE"
    assert result["exclusions"]["gross_fee_net_identity_mismatch"] == 1
    assert result["exclusions"]["missing_or_invalid_cost_components"] == 1
    assert result["exclusions"]["other_market"] == 1


def test_duplicates_are_not_additional_trades_and_conflicts_fail_closed():
    row = lot(1, 4)
    assert diagnose([row, deepcopy(row)])["qualified_entry_lots"] == 1
    conflict = {**row, "net_pnl": 50}
    assert diagnose([row, conflict])["qualified_entry_lots"] == 0


def test_correlated_profitable_trades_are_not_declared_an_independent_edge():
    rows = [{**lot(i, 4), "entry_time": trade(1)["entry_time"]} for i in range(50)]
    report = diagnose(rows)
    assert report["expectancy"]["episode_windows"] == 1
    assert report["expectancy"]["retrospective_lower_bound"] is None
    assert report["promotion_action"] == "NONE"


def test_brain_shows_only_persisted_worker_diagnosis():
    from pathlib import Path
    page = Path("pages/3_Oracle_Brain.py").read_text()
    assert 'report.get("economic_diagnosis")' in page
    assert "Post-cost economic diagnosis has not been produced" in page
    assert "Winner formula:" not in page
    assert 'report.get("winner_edge_research")' in page
    assert "Unexecuted opportunities are not inferred" in page
