"""Fast scans must not silently drop held positions under candidate caps."""
from unittest.mock import patch

import market_worker


def test_fast_scan_retains_all_held_symbols_when_budget_is_smaller():
    held = {"HOLD-A", "HOLD-B", "HOLD-C", "HOLD-D"}
    watchlist = {symbol: symbol for symbol in held}
    watchlist.update({"NEW-A": "New A", "NEW-B": "New B"})
    with (
        patch.object(market_worker, "WATCHLISTS", {"crypto": watchlist}),
        patch.object(market_worker, "_held_symbols", return_value=held),
        patch.object(market_worker, "_latest_ranked_symbols", return_value=["NEW-A", "NEW-B"]),
        patch.object(market_worker, "_rolling_batch", return_value=[("NEW-A", "New A")]),
        patch.object(market_worker, "FAST_SCAN_BATCH_SIZE", 2),
        patch.object(market_worker, "FAST_SCAN_TOP_RANKED", 2),
    ):
        selected = market_worker._fast_candidate_batch("crypto")
    assert {symbol for symbol, _ in selected} == held
    assert len(selected) == 4


def test_fast_scan_keeps_ranked_candidates_with_available_capacity():
    with (
        patch.object(market_worker, "WATCHLISTS", {"crypto": {"HOLD": "Held", "NEW": "New"}}),
        patch.object(market_worker, "_held_symbols", return_value={"HOLD"}),
        patch.object(market_worker, "_latest_ranked_symbols", return_value=["NEW"]),
        patch.object(market_worker, "_rolling_batch", return_value=[]),
        patch.object(market_worker, "FAST_SCAN_BATCH_SIZE", 2),
        patch.object(market_worker, "FAST_SCAN_TOP_RANKED", 2),
    ):
        selected = market_worker._fast_candidate_batch("crypto")
    assert [symbol for symbol, _ in selected] == ["HOLD", "NEW"]
