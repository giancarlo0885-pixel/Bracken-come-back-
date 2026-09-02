from __future__ import annotations

from market_data import MarketSnapshot
from robinhood_current_marketdata_runtime import (
    overlay_execution_payload,
    snapshot_from_robinhood_quote,
)


def test_snapshot_from_robinhood_quote_builds_verified_mid_and_spread():
    snapshot = snapshot_from_robinhood_quote(
        "BTC-USD",
        {"symbol": "BTC-USD", "bid": "100.00", "ask": "100.20"},
        fetched_at="2026-09-02T23:59:00+00:00",
    )

    assert snapshot is not None
    assert snapshot.symbol == "BTC-USD"
    assert snapshot.price == 100.10
    assert snapshot.bid == 100.0
    assert snapshot.ask == 100.2
    assert snapshot.provider == "Robinhood Crypto"
    assert snapshot.quote_verified is True
    assert snapshot.provider_quote_verified is True
    assert snapshot.stale is False
    assert snapshot.timestamp == "2026-09-02T23:59:00+00:00"
    assert snapshot.spread_pct is not None
    assert 0 < snapshot.spread_pct < 1


def test_snapshot_rejects_symbol_mismatch_or_invalid_book():
    assert snapshot_from_robinhood_quote(
        "BTC-USD",
        {"symbol": "ETH-USD", "bid": "100", "ask": "101"},
    ) is None
    assert snapshot_from_robinhood_quote(
        "BTC-USD",
        {"symbol": "BTC-USD", "bid": "101", "ask": "100"},
    ) is None


def test_overlay_preserves_analysis_provenance_and_promotes_robinhood_current_mark():
    snapshot = MarketSnapshot(
        symbol="ETH-USD",
        price=2500.5,
        change_pct=0.0,
        volume=0.0,
        timestamp="2026-09-02T23:59:00+00:00",
        bid=2500.0,
        ask=2501.0,
        provider="Robinhood Crypto",
        interval="1m",
        fetched_at="2026-09-02T23:59:00+00:00",
        requested_symbol="ETH-USD",
        provider_symbol="ETH-USD",
        provider_native_symbol="ETH-USD",
        quote_verified=True,
        stale=False,
        spread_pct=0.04,
        source_capability="best_bid_ask_realtime",
        source_identity="Robinhood Crypto:ETH-USD:best_bid_ask",
        cache_identity="robinhood_crypto_best_bid_ask:ETH-USD",
        provider_quote_verified=True,
        paper_reference_verified=False,
        verification_basis="provider:robinhood_crypto_best_bid_ask_read_time",
    )
    original = {
        "symbol": "ETH-USD",
        "market": "crypto",
        "price": 2492.0,
        "provider": "Yahoo Finance",
        "quote_timestamp": "2026-09-02T23:55:00+00:00",
        "source_interval": "5m",
        "quote_verified": True,
        "avg_dollar_volume": 1_000_000.0,
        "provider_support": ["Yahoo Finance"],
    }

    enriched = overlay_execution_payload(original, snapshot)

    assert enriched["analysis_price"] == 2492.0
    assert enriched["analysis_provider"] == "Yahoo Finance"
    assert enriched["analysis_quote_timestamp"] == "2026-09-02T23:55:00+00:00"
    assert enriched["price"] == 2500.5
    assert enriched["bid"] == 2500.0
    assert enriched["ask"] == 2501.0
    assert enriched["provider"] == "Robinhood Crypto"
    assert enriched["quote_verified"] is True
    assert enriched["provider_quote_verified"] is True
    assert enriched["stale"] is False
    assert enriched["avg_dollar_volume"] == 1_000_000.0
    assert "Robinhood Crypto" in enriched["provider_support"]
    assert enriched["current_data_verified"] is True
