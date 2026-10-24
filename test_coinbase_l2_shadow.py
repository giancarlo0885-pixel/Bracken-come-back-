"""Isolated Exchange Level2 fixtures: no WebSocket, HTTP, database or trades."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from coinbase_l2_shadow import (
    CoinbaseL2ShadowBooks,
    MAX_CHANGES,
    MAX_LEVELS_PER_SIDE,
    SOURCE,
)

NOW = datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc)


def snapshot(symbol="BTC-USD"):
    return {
        "type": "snapshot", "product_id": symbol,
        "bids": [["100.00000001", "2"], ["99", "3"]],
        "asks": [["100.00000002", "4"], ["101", "5"]],
    }


def update(symbol="BTC-USD", *, time=NOW, changes=None):
    return {
        "type": "l2update", "product_id": symbol,
        "time": time.isoformat(),
        "changes": changes if changes is not None else [["buy", "100.00000001", "3"]],
    }


def ready():
    books = CoinbaseL2ShadowBooks()
    assert books.ingest(snapshot(), received_at=NOW)
    assert books.ingest(update(), received_at=NOW)
    return books


def test_snapshot_does_not_derive_freshness_from_local_receipt():
    books = CoinbaseL2ShadowBooks()
    assert books.ingest(snapshot(), received_at=NOW)
    assert books.shadow_reference("BTC-USD", now=NOW) is None
    assert books.ingest({"type": "heartbeat", "product_id": "BTC-USD", "time": NOW.isoformat()},
                        received_at=NOW) is False


def test_first_sourced_update_is_observational_only():
    books = ready()
    mark = books.shadow_reference("BTC-USD", now=NOW)
    assert mark["bid"] == "100.00000001"
    assert mark["ask"] == "100.00000002"
    assert mark["bid_quantity"] == "3"
    assert mark["quote_timestamp"] == NOW.isoformat()
    assert mark["timestamp_basis"] == "provider_l2update"
    assert mark["provider"] == SOURCE
    assert mark["reference_only"] is True
    assert mark["execution_authority"] == "NONE"
    assert mark["broker_submission"] == "NONE"
    assert all(mark[k] is False for k in (
        "quote_verified", "provider_quote_verified",
        "paper_reference_verified", "execution_quote_eligible"))


def test_equal_timestamp_updates_and_zero_quantity_remove_level():
    books = ready()
    assert books.ingest(update(changes=[
        ["buy", "100.00000001", "0"],
        ["sell", "100.00000002", "0.000000"],
        ["sell", "100.100", "1"],
    ]), received_at=NOW)
    quote = books.shadow_reference("BTC-USD", now=NOW)
    assert quote["bid"] == "99"
    assert quote["ask"] == "100.100"


def test_snapshot_resets_old_stream_but_never_grants_fresh_mark():
    books = ready()
    assert books.ingest(snapshot(), received_at=NOW + timedelta(seconds=1))
    assert books.shadow_reference("BTC-USD", now=NOW + timedelta(seconds=1)) is None
    assert books.ingest(update(time=NOW + timedelta(seconds=2)), received_at=NOW + timedelta(seconds=2))
    assert books.shadow_reference("BTC-USD", now=NOW + timedelta(seconds=2))


def test_quote_expires_without_source_updates_and_heartbeat_does_not_refresh():
    books = ready()
    assert not books.ingest({"type": "heartbeat", "product_id": "BTC-USD",
                             "time": (NOW + timedelta(seconds=5)).isoformat()},
                            received_at=NOW + timedelta(seconds=5))
    assert books.shadow_reference("BTC-USD", now=NOW + timedelta(seconds=6)) is None


@pytest.mark.parametrize("bad", [
    {"changes": [["buy", "100", "3"], ["sell", "100", "3"]]},
    {"changes": [["buy", "101", "3"]]},
    {"changes": [["sell", "0", "1"]]},
    {"changes": [["buy", "100", "-1"]]},
    {"changes": [["buy", "100", "NaN"]]},
    {"changes": [["buy", "100", "1e10"]]},
    {"changes": [["buy", "100", float("inf")]]},
    {"changes": [["bid", "100", "1"]]},
    {"changes": [["buy", "100"]]},
    {"changes": []},
    {"changes": [["buy", "100", "1"]] * (MAX_CHANGES + 1)},
    {"time": None},
    {"time": (NOW - timedelta(seconds=20)).isoformat()},
    {"time": (NOW + timedelta(seconds=3)).isoformat()},
])
def test_bad_update_invalidates_book_and_cannot_be_used(bad):
    books = ready()
    altered = {**update(), **bad}
    assert not books.ingest(altered, received_at=NOW)
    assert books.shadow_reference("BTC-USD", now=NOW) is None
    assert not books.ingest(update(), received_at=NOW)


@pytest.mark.parametrize("field,value", [
    ("bids", []),
    ("asks", []),
    ("bids", [["100", "1"], ["100.00", "2"]]),
    ("asks", [["99", "1"]]),
    ("asks", [["100.00000001", "2"]]),
    ("bids", [["100", "0"]]),
    ("bids", [["100", "NaN"]]),
    ("bids", [["100", "2"], ["99", "3"]] * (MAX_LEVELS_PER_SIDE // 2 + 1)),
])
def test_malformed_snapshots_fail_closed(field, value):
    books = ready()
    message = snapshot()
    message[field] = value
    assert not books.ingest(message, received_at=NOW)
    assert books.shadow_reference("BTC-USD", now=NOW) is None


def test_regressing_timestamp_invalidates_and_requires_new_snapshot():
    books = ready()
    previous = NOW - timedelta(seconds=1)
    assert not books.ingest(update(time=previous), received_at=NOW)
    assert not books.ingest(update(time=NOW), received_at=NOW)
    assert books.ingest(snapshot(), received_at=NOW)
    assert books.ingest(update(time=NOW), received_at=NOW)


def test_update_without_snapshot_and_unsupported_symbols_are_isolated():
    books = CoinbaseL2ShadowBooks()
    assert not books.ingest(update(), received_at=NOW)
    assert not books.ingest(snapshot("PAXG-USD"), received_at=NOW)
    assert books.ingest(snapshot("ETH-USD"), received_at=NOW)
    assert books.ingest(update("ETH-USD"), received_at=NOW)
    assert books.shadow_reference("ETH-USD", now=NOW)
    assert books.shadow_reference("BTC-USD", now=NOW) is None
    assert books.shadow_reference("PAXG-USD", now=NOW) is None


def test_wire_disconnect_clears_every_book():
    books = ready()
    books.disconnected()
    assert books.shadow_reference("BTC-USD", now=NOW) is None
    assert not books.ingest(update(), received_at=NOW)


def test_no_unbounded_symbols_and_no_client_or_broker_fields():
    with pytest.raises(ValueError):
        CoinbaseL2ShadowBooks(["PAXG-USD"])
    mark = ready().shadow_reference("BTC-USD", now=NOW)
    assert "book" not in mark
    assert "paper_fill_price" not in mark
    assert "quote_id" not in mark
    from quote_provenance import provider_verified
    assert not provider_verified(mark)
