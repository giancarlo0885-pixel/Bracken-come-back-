"""Read-only FIFO research must not confuse independent market namespaces."""
from datetime import timedelta

import pytest

from paper_exit_research import reconcile_fifo
from test_paper_exit_research import START, buy, sell


AS_OF = START + timedelta(days=1)


def _market(row, market):
    return {**row, "market": market}


def test_reused_trade_ids_across_markets_do_not_suppress_valid_fifo_closes():
    """Cash and crypto can use the same IDs without sharing their inventories."""
    cash_buy = _market(buy("shared-buy", 1, 100, 0.1), "cash")
    cash_sell = _market(sell("shared-sell", 1, 100, 102, 0.1, 2), "cash")
    crypto_buy = _market(buy("shared-buy", 1, 200, 0.1), "crypto")
    crypto_sell = _market(sell("shared-sell", 1, 200, 203, 0.1, 3), "crypto")

    report = reconcile_fifo(
        [cash_buy, crypto_buy, cash_sell, crypto_sell], as_of=AS_OF
    )

    assert report["observed_closes"] == 2
    assert report["qualified_fifo_closes"] == 2
    assert report["net_pnl"] == pytest.approx(4.6)
    assert {row["market"] for row in report["closes"]} == {"cash", "crypto"}
    assert report["diagnostics"].get("conflicting_fill_identity", 0) == 0


def test_conflicting_id_within_market_still_taints_only_affected_inventory():
    cash_buy = _market(buy("bad-buy", 1, 100, 0.1), "cash")
    cash_conflict = {**cash_buy, "entry_price": 99}
    cash_sell = _market(sell("bad-sell", 1, 100, 102, 0.1, 2), "cash")
    crypto_buy = _market(buy("good-buy", 1, 200, 0.1), "crypto")
    crypto_sell = _market(sell("good-sell", 1, 200, 203, 0.1, 3), "crypto")

    report = reconcile_fifo(
        [cash_buy, cash_conflict, cash_sell, crypto_buy, crypto_sell], as_of=AS_OF
    )

    assert report["diagnostics"]["conflicting_fill_identity"] == 1
    assert report["qualified_fifo_closes"] == 1
    assert report["closes"][0]["market"] == "crypto"
    assert report["net_pnl"] == pytest.approx(2.8)


def test_identical_duplicate_in_same_market_remains_deduplicated():
    opening = buy("same-buy", 1, 100, 0.1)
    closing = sell("same-sell", 1, 100, 102, 0.1, 2)
    report = reconcile_fifo([opening, opening.copy(), closing], as_of=AS_OF)
    assert report["diagnostics"]["duplicate_fill"] == 1
    assert report["qualified_fifo_closes"] == 1
