"""Research identity is scoped to the market as well as the original trade ID.

Synthetic completed trades only; no broker, database, or production mutation.
"""
from paper_winner_edge_research import prepare, discover
from test_paper_winner_edge_research import AS_OF, CUTOFF, trade


def test_same_trade_id_in_cash_and_crypto_preserves_both_qualified_outcomes():
    crypto = trade(7, pnl=1.25)
    cash = {**trade(7, pnl=-2.5), "market": "cash"}
    valid, excluded = prepare([crypto, cash], AS_OF)
    assert {(r["market"], r["trade_id"], r["net_pnl"]) for r in valid} == {
        ("crypto", "7", 1.25), ("cash", "7", -2.5)
    }
    assert excluded.get("missing_or_conflicting_identity", 0) == 0
    report = discover([crypto, cash], cutoff=CUTOFF, as_of=AS_OF)
    assert report["search_cohorts"] == 2
    assert {c["cohort"]["market"] for c in report["candidates"]} == {"crypto", "cash"}


def test_conflicting_versions_within_one_market_remain_excluded():
    original = trade(8, pnl=1.25)
    correction = {**original, "net_pnl": -3.0}
    valid, excluded = prepare([original, correction], AS_OF)
    assert not valid
    assert excluded["missing_or_conflicting_identity"] == 2


def test_identical_same_market_duplicates_still_deduplicate():
    original = trade(9, pnl=-1.0)
    valid, excluded = prepare([original, original], AS_OF)
    assert len(valid) == 1
    assert excluded["duplicate"] == 1
