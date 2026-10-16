from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from kraken_reference_book import (ReferenceBooks, BookIntegrityError, SYMBOLS,
    book_checksum, compare_reference, exact_decimal)
from kraken_reference_runtime import display_state, reconnect_delay

NOW = datetime(2026, 10, 16, 20, 30, tzinfo=timezone.utc)


def ack(symbol="BTC/USD"):
    return json.dumps({"method": "subscribe", "success": True, "result": {
        "channel": "book", "symbol": symbol, "depth": 10, "snapshot": True}})


def message(bids=None, asks=None, *, kind="snapshot", symbol="BTC/USD", checksum=None, when=NOW):
    bids = bids if bids is not None else {"100.0": "1.00000000"}
    asks = asks if asks is not None else {"101.0": "2.00000000"}
    if checksum is None:
        checksum = book_checksum({Decimal(p): Decimal(q) for p, q in bids.items() if Decimal(q)},
                                 {Decimal(p): Decimal(q) for p, q in asks.items() if Decimal(q)})
    return json.dumps({"channel": "book", "type": kind, "data": [{"symbol": symbol,
        "bids": [{"price": p, "qty": q} for p, q in bids.items()],
        "asks": [{"price": p, "qty": q} for p, q in asks.items()],
        "checksum": checksum, "timestamp": when.isoformat()}]})


def ready(symbol="BTC/USD"):
    state = ReferenceBooks()
    state.opened(NOW)
    state.ingest(ack(symbol), NOW)
    state.ingest(message(symbol=symbol), NOW)
    return state


def test_official_kraken_crc32_vector_preserves_trailing_zero_precision():
    # Official book-checksum-v2 example. Independent fixed expected checksum.
    bids = dict(zip(map(Decimal, ["45283.5", "45283.4", "45282.1", "45281.0", "45280.3",
        "45279.0", "45277.6", "45277.5", "45277.3", "45276.6"]), map(Decimal, [
        "0.10000000", "1.54582015", "0.10000000", "0.10000000", "1.54592586",
        "0.07990000", "0.03310103", "0.30000000", "1.54602737", "0.15445238"])))
    asks = dict(zip(map(Decimal, ["45285.2", "45286.4", "45286.6", "45289.6", "45290.2",
        "45291.8", "45294.7", "45296.1", "45297.5", "45299.5"]), map(Decimal, [
        "0.00100000", "1.54571953", "1.54571109", "1.54560911", "0.15890660",
        "1.54553491", "0.04454749", "0.35380000", "0.09945542", "0.18772827"])))
    assert book_checksum(bids, asks) == 3310070434
    asks[Decimal("45285.2")] = Decimal("0.001")
    assert book_checksum(bids, asks) != 3310070434


@pytest.mark.parametrize("symbol,internal", SYMBOLS.items())
def test_snapshot_symbol_times_sizes_and_shadow_authority(symbol, internal):
    quote = ready(symbol).quote(symbol, NOW)
    assert quote["symbol"] == internal and quote["exchange_symbol"] == symbol
    assert quote["bid_quantity"] == "1.00000000" and quote["ask_quantity"] == "2.00000000"
    assert quote["checksum_status"] == "VALID"
    assert quote["quote_timestamp"] == NOW.isoformat() == quote["received_at"]
    assert quote["execution_authority"] == "NONE" and quote["reference_only"] is True
    for field in ("verified", "quote_verified", "provider_quote_verified", "paper_reference_verified",
                  "execution_quote_eligible", "quote_eligible"):
        assert quote[field] is False


def test_wire_json_numbers_never_round_trip_through_binary_float():
    raw = message().replace('"100.0"', '100.0').replace('"1.00000000"', '1.00000000')
    state = ReferenceBooks(); state.opened(NOW); state.ingest(ack(), NOW)
    state.ingest(raw, NOW)
    assert state.quote("BTC/USD", NOW)["bid_quantity"] == "1.00000000"


def test_sequential_updates_deletion_and_identical_exchange_timestamps():
    state = ready()
    state.ingest(message({"100.0": "3.00000000"}, {}, kind="update", checksum=book_checksum(
        {Decimal("100.0"): Decimal("3.00000000")}, {Decimal("101.0"): Decimal("2.00000000")})), NOW)
    assert state.quote("BTC/USD", NOW)["bid_quantity"] == "3.00000000"
    state.ingest(message({"100.0": "0", "99.0": "5.00"}, {}, kind="update", checksum=book_checksum(
        {Decimal("99.0"): Decimal("5.00")}, {Decimal("101.0"): Decimal("2.00000000")})), NOW)
    assert state.quote("BTC/USD", NOW)["bid"] == "99.0"
    assert Decimal("100.0") not in state.books["BTC/USD"]["bids"]


def test_repeated_levels_applied_in_order_before_truncation():
    state = ready()
    msg = json.loads(message({"99.0": "1.0"}, {}, kind="update", checksum=book_checksum(
        {Decimal("99.0"): Decimal("7.000")}, {Decimal("101.0"): Decimal("2.00000000")})))
    msg["data"][0]["bids"] = [{"price": "100.0", "qty": "0"},
        {"price": "99.0", "qty": "1.0"}, {"price": "99.0", "qty": "7.000"}]
    state.ingest(json.dumps(msg), NOW)
    assert state.quote("BTC/USD", NOW)["bid_quantity"] == "7.000"


def test_depth_truncation_does_not_resurrect_out_of_scope_levels():
    bids = {str(p): "1.0" for p in range(100, 90, -1)}
    asks = {str(p): "1.0" for p in range(110, 120)}
    state = ReferenceBooks(); state.opened(NOW); state.ingest(ack(), NOW)
    state.ingest(message(bids, asks), NOW)
    expected = {Decimal(p): Decimal("1.0") for p in range(101, 91, -1)}
    state.ingest(message({"101": "1.0"}, {}, kind="update", checksum=book_checksum(
        expected, {Decimal(p): Decimal(q) for p, q in asks.items()})), NOW)
    assert len(state.books["BTC/USD"]["bids"]) == 10
    assert Decimal(91) not in state.books["BTC/USD"]["bids"]
    expected.pop(Decimal(101))
    state.ingest(message({"101": "0"}, {}, kind="update", checksum=book_checksum(
        expected, {Decimal(p): Decimal(q) for p, q in asks.items()})), NOW)
    assert len(state.books["BTC/USD"]["bids"]) == 9


@pytest.mark.parametrize("reason,raw", [
    ("checksum_failures", message(checksum=0)),
    ("crossed_book_rejections", message({"102": "1"}, {"101": "1"})),
    ("stale_rejections", message(when=NOW-timedelta(seconds=11))),
    ("stale_rejections", message(when=NOW+timedelta(seconds=3))),
    ("unexpected_symbol", message(symbol="XBT/USD")),
    ("subscription_error", '{"method":"subscribe","success":false,"error":"Invalid symbol"}'),
    ("exchange_unavailable", '{"channel":"status","data":[{"system":"maintenance"}]}'),
])
def test_integrity_failure_invalidates_all_books_until_new_connection_snapshot(reason, raw):
    state = ready()
    with pytest.raises(BookIntegrityError, match=reason): state.ingest(raw, NOW)
    assert state.quote("BTC/USD", NOW) is None and not state.books
    assert state.counts[reason] == 1
    state.opened(NOW); state.ingest(ack(), NOW)
    with pytest.raises(BookIntegrityError, match="snapshot_required"):
        state.ingest(message(kind="update"), NOW)
    state.opened(NOW); state.ingest(ack(), NOW); state.ingest(message(), NOW)
    assert state.quote("BTC/USD", NOW) is not None


@pytest.mark.parametrize("value", [1.25, True, "NaN", "Infinity", "1e100000", "garbage"])
def test_bad_precision_and_non_finite_rejected(value):
    with pytest.raises(BookIntegrityError): exact_decimal(value)


def test_timestamp_regression_rejected_but_equal_timestamps_supported():
    state = ready()
    with pytest.raises(BookIntegrityError, match="timestamp_regression"):
        state.ingest(message(kind="update", when=NOW-timedelta(seconds=1)), NOW)


def test_heartbeats_do_not_refresh_book_or_last_valid_update():
    state = ready(); later = NOW+timedelta(seconds=11)
    state.ingest('{"channel":"heartbeat"}', later)
    assert state.connected and state.quote("BTC/USD", later) is None
    state.quote("BTC/USD", later)
    assert state.counts["stale_rejections"] == 1
    assert state.last_valid["BTC/USD"]["exchange_timestamp"] == NOW.isoformat()
    state.invalidate()
    assert state.quote("BTC/USD", NOW) is None


def primary(**overrides):
    return {"symbol": "BTC-USD", "provider": "Robinhood Crypto", "bid": 100, "ask": 101,
            "quote_timestamp": NOW.isoformat(), **overrides}


def test_comparison_is_signed_diagnostic_not_price_equality_gate():
    quote = ready().quote("BTC/USD", NOW)
    result = compare_reference(quote, primary(bid=90, ask=91), NOW)
    assert result["status"] == "ALIGNED_VENUE_COMPARISON"
    assert Decimal(result["mid_difference_bps"]) > 1000
    assert result["execution_impact"] == "NONE"


@pytest.mark.parametrize("changes", [{"symbol": "ETH-USD"}, {"bid": 102}, {"stale": True},
    {"quote_timestamp": (NOW-timedelta(seconds=3)).isoformat()}, {"bid": None}])
def test_unaligned_or_invalid_primary_never_produces_divergence(changes):
    result = compare_reference(ready().quote("BTC/USD", NOW), primary(**changes), NOW)
    assert "mid_difference_bps" not in result


def test_reference_only_cannot_gain_paper_or_live_execution_authority():
    from quote_provenance import provider_verified, reference_allowed_in_mode, quote_quality_rejection_reason
    quote = ready().quote("BTC/USD", NOW)
    # Even an accidental legacy verification alias cannot promote reference-only data.
    quote.update(verified=True, quote_verified=True, provider_quote_verified=True, paper_reference_verified=True)
    assert provider_verified(quote) is False
    for mode in ("paper", "live"):
        assert reference_allowed_in_mode(quote, mode, True) is False
    assert quote_quality_rejection_reason(quote) == "quote explicitly ineligible for execution"
    quote.pop("reference_only")
    assert not provider_verified(quote)  # source identity also preserves the boundary
    assert not reference_allowed_in_mode(quote, "paper", True)


def test_shared_snapshot_is_revalidated_at_display_time():
    state = ready(); payload = state.health(NOW)
    assert display_state(payload, NOW, NOW)["quotes"]["BTC-USD"]["fresh"]
    # Fresh publisher cannot make an old cached quote live.
    later = NOW+timedelta(seconds=11)
    assert not display_state(payload, later, later)["quotes"]
    assert display_state(payload, NOW, later)["status"] == "PUBLISHER_STALE"
    assert payload["quotes"]  # reader does not mutate stored evidence


def test_bounded_jitter_and_backoff():
    assert reconnect_delay(0, lambda a,b: a) == 5
    assert reconnect_delay(1, lambda a,b: a) == 8
    for attempt in (2, 10, 100000):
        assert 5 <= reconnect_delay(attempt) <= 60
