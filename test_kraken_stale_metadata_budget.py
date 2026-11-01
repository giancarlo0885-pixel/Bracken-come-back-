"""Fail-closed Kraken paper pricing must not spend HTTP metadata budget on ineligible books.

Synthetic reference, clock, and instrument provider. No external calls or fills.
"""
from datetime import timedelta

import pytest

from test_kraken_paper_pricing import NOW, provider


@pytest.mark.parametrize("timestamp_field", ["quote_timestamp", "received_at"])
@pytest.mark.parametrize("age_seconds", [-6, 1])
def test_stale_or_future_reference_rejected_before_metadata_request(
    monkeypatch, timestamp_field, age_seconds
):
    pricing, snapshot = provider(monkeypatch)
    snapshot["quotes"]["BTC-USD"][timestamp_field] = (
        NOW + timedelta(seconds=age_seconds)
    ).isoformat()

    assert pricing.quote("BTC-USD") is None
    pricing.data.instruments.assert_not_called()
    assert pricing.receipts == {}
    assert pricing.last_health["BTC-USD"]["reason"] == "STALE_KRAKEN_PAPER_BOOK"


@pytest.mark.parametrize("bad_reference,expected_reason", [
    (["malformed"], "KRAKEN_SYMBOL_OR_BOOK_INVALID"),
    ({"checksum_status": "FAILED"}, "KRAKEN_REFERENCE_IDENTITY_OR_AUTHORITY_INVALID"),
    ({"execution_authority": "PAPER_SIMULATION_ONLY"}, "KRAKEN_REFERENCE_IDENTITY_OR_AUTHORITY_INVALID"),
])
def test_invalid_reference_identity_does_not_reserve_metadata(
    monkeypatch, bad_reference, expected_reason
):
    pricing, snapshot = provider(monkeypatch)
    if isinstance(bad_reference, list):
        snapshot["quotes"]["BTC-USD"] = bad_reference
    else:
        snapshot["quotes"]["BTC-USD"].update(bad_reference)

    assert pricing.quote("BTC-USD") is None
    pricing.data.instruments.assert_not_called()
    assert pricing.receipts == {}
    assert pricing.last_health["BTC-USD"]["reason"] == expected_reason
