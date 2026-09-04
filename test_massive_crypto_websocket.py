from __future__ import annotations

import pytest

from massive_crypto_websocket import MassiveCryptoStream, build_subscriptions


NOW_MS = 1_700_000_000_000


def test_watchlist_subscriptions_are_explicit_and_deduplicated():
    subscriptions = build_subscriptions(
        ["BTC-USD", "ETH-USD", "BTC-USD"],
        scope="watchlist",
        channels=("XQ", "XT", "XAS"),
    )
    assert subscriptions == (
        "XQ.BTC-USD",
        "XQ.ETH-USD",
        "XT.BTC-USD",
        "XT.ETH-USD",
        "XAS.BTC-USD",
        "XAS.ETH-USD",
    )


def test_all_scope_uses_provider_wildcards():
    assert build_subscriptions(["BTC-USD"], scope="all", channels=("XQ", "XT")) == (
        "XQ.*",
        "XT.*",
    )


def test_quote_reference_uses_median_across_exchanges():
    stream = MassiveCryptoStream("test-key", ("XQ.BTC-USD",), max_age_seconds=10)
    stream.ingest_payload(
        [
            {"ev": "XQ", "pair": "BTC-USD", "bp": 99.0, "ap": 101.0, "t": NOW_MS - 1000, "x": 1},
            {"ev": "XQ", "pair": "BTC-USD", "bp": 101.0, "ap": 103.0, "t": NOW_MS - 500, "x": 2},
        ]
    )

    reference = stream.reference("BTC-USD", now_ms=NOW_MS)

    assert reference is not None
    assert reference["provider"] == "Massive Crypto WebSocket"
    assert reference["event_type"] == "XQ"
    assert reference["exchange_count"] == 2
    assert reference["quote_verified"] is True
    assert reference["price"] == pytest.approx(101.0)
    assert reference["bid"] == pytest.approx(100.0)
    assert reference["ask"] == pytest.approx(102.0)


def test_stale_quote_is_not_promoted_to_reference():
    stream = MassiveCryptoStream("test-key", ("XQ.BTC-USD",), max_age_seconds=5)
    stream.ingest_payload(
        {"ev": "XQ", "pair": "BTC-USD", "bp": 99.0, "ap": 101.0, "t": NOW_MS - 10_000, "x": 1}
    )
    assert stream.reference("BTC-USD", now_ms=NOW_MS) is None


def test_second_aggregate_is_fallback_when_quote_is_unavailable():
    stream = MassiveCryptoStream("test-key", ("XAS.BTC-USD",), max_age_seconds=10)
    stream.ingest_payload(
        {"ev": "XAS", "pair": "BTC-USD", "c": 100.25, "s": NOW_MS - 1500, "e": NOW_MS - 500}
    )

    reference = stream.reference("BTC-USD", now_ms=NOW_MS)

    assert reference is not None
    assert reference["event_type"] == "XAS"
    assert reference["price"] == pytest.approx(100.25)
    assert reference["bid"] is None
    assert reference["ask"] is None
