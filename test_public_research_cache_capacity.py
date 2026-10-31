"""Shared public research cache must hold the bounded four-symbol endpoint set.

Synthetic HTTP/clock/ledger fixtures only; no exchange calls or trade authority.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import kraken_research_data as data


def test_four_symbol_mixed_venue_cache_does_not_evict_fresh_metadata(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(data.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(data, "provider_cooldown_active_live", lambda _: {"active": False})
    reserve = Mock(return_value={"reserved": True})
    monkeypatch.setattr(data, "reserve_provider_budget_live", reserve)

    def response(url, **_):
        payload = (
            {"error": [], "result": {"BTC/USD": {"status": "online"}}}
            if "api.kraken.com" in url
            else {"source": "coinbase"}
        )
        return SimpleNamespace(status_code=200, json=lambda: payload)

    get = Mock(side_effect=response)
    client = data.PublicResearchData(get)
    symbols = ("BTC", "ETH", "AAVE", "ADA")

    # One Kraken AssetPairs key, four Kraken OHLC keys, four Coinbase L1
    # keys and four Coinbase candle keys = 13 concurrent bounded keys.
    metadata = client.instruments()
    receipt = client.last_receipt
    for symbol in symbols:
        client.request("Kraken Public Research", "OHLC",
                       {"pair": f"{symbol}/USD", "assetVersion": 1, "interval": 5})
        client.request("Coinbase Public Research", f"products/{symbol}-USD/book", {"level": 1})
        client.request("Coinbase Public Research", f"products/{symbol}-USD/candles",
                       {"granularity": 300})

    assert len(client.cache) == 13
    assert get.call_count == reserve.call_count == 13
    assert client.instruments() is metadata
    assert client.last_receipt == receipt
    assert get.call_count == reserve.call_count == 13

    # Capacity growth cannot bypass shared budget, nor extend metadata or
    # short-lived book freshness. Cached responses retain original receipts.
    reserve.return_value = {"reserved": False, "reason": "provider capability budget exhausted"}
    clock[0] += 2.1
    with pytest.raises(data.ResearchUnavailable, match="PROVIDER_BUDGET_EXHAUSTED"):
        client.request("Coinbase Public Research", "products/BTC-USD/book", {"level": 1})
    assert get.call_count == 13
    assert client.instruments() is metadata
    clock[0] += 298.0
    with pytest.raises(data.ResearchUnavailable, match="PROVIDER_BUDGET_EXHAUSTED"):
        client.instruments()
    assert get.call_count == 13
