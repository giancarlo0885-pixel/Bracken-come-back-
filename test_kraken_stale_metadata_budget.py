"""Fail-closed Kraken paper pricing must not spend HTTP metadata budget on stale books.

Synthetic reference, clock, and instrument provider. No external calls or fills.
"""
from datetime import timedelta

import pytest

from test_kraken_paper_pricing import NOW, provider


@pytest.mark.parametrize("age_seconds", [-6, 1])
def test_stale_or_future_reference_rejected_before_metadata_request(monkeypatch, age_seconds):
    pricing, snapshot = provider(monkeypatch)
    snapshot["quotes"]["BTC-USD"]["quote_timestamp"] = (
        NOW + timedelta(seconds=age_seconds)
    ).isoformat()

    assert pricing.quote("BTC-USD") is None
    pricing.data.instruments.assert_not_called()
    assert pricing.receipts == {}
    assert pricing.last_health["BTC-USD"]["reason"] == "STALE_KRAKEN_PAPER_BOOK"
