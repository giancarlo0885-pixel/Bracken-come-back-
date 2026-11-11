"""Read-only bounded request attribution for the shared public-data client.

All requests, responses and provider ledgers are synthetic. No exchange or broker access.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import kraken_research_data as data


def test_endpoint_budget_attribution_separates_hits_reservations_and_denials(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(data.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(data, "provider_cooldown_active_live", lambda _: {"active": False})
    reserve = Mock(return_value={"reserved": True})
    monkeypatch.setattr(data, "reserve_provider_budget_live", reserve)

    def response(url, **_):
        payload = ({"error": [], "result": {"BTC/USD": {"status": "online"}}}
                   if "api.kraken.com" in url else {"time": "2026-10-31T00:00:00Z"})
        return SimpleNamespace(status_code=200, json=lambda: payload)

    get = Mock(side_effect=response)
    client = data.PublicResearchData(get)
    metadata = client.instruments()
    first_receipt = client.last_receipt
    assert client.instruments() is metadata
    assert client.last_receipt == first_receipt
    client.request("Kraken Public Research", "OHLC",
                   {"pair": "BTC/USD", "assetVersion": 1, "interval": 5})
    client.request("Coinbase Public Research", "products/BTC-USD/book", {"level": 1})
    clock[0] += 2.1
    reserve.return_value = {"reserved": False, "reason": "provider capability budget exhausted"}
    with pytest.raises(data.ResearchUnavailable, match="PROVIDER_BUDGET_EXHAUSTED"):
        client.request("Coinbase Public Research", "products/BTC-USD/book", {"level": 1})

    snapshot = client.drain_request_diagnostics()
    assert snapshot["scope"] == "PROCESS_LOCAL_CLIENT"
    by_class = {(row["provider"], row["endpoint"]): row for row in snapshot["rows"]}
    assert set(by_class) == {("KRAKEN", "ASSET_PAIRS"), ("KRAKEN", "OHLC"),
                             ("COINBASE", "L1_BOOK")}
    assert by_class["KRAKEN", "ASSET_PAIRS"] == {
        "provider": "KRAKEN", "endpoint": "ASSET_PAIRS",
        "cache_hits": 1, "cache_misses": 1,
        "reservation_attempts": 1, "reservations_granted": 1, "reservations_denied": 0}
    assert by_class["KRAKEN", "OHLC"]["reservations_granted"] == 1
    assert by_class["COINBASE", "L1_BOOK"]["cache_misses"] == 2
    assert by_class["COINBASE", "L1_BOOK"]["reservation_attempts"] == 2
    assert by_class["COINBASE", "L1_BOOK"]["reservations_denied"] == 1
    assert get.call_count == 3 and reserve.call_count == 4
    assert "BTC-USD" not in str(snapshot)
    assert "api.kraken.com" not in str(snapshot)
    assert client.drain_request_diagnostics() == {"scope": "PROCESS_LOCAL_CLIENT", "rows": []}


def test_endpoint_attribution_is_bounded_by_classes_not_symbol_cardinality(monkeypatch):
    monkeypatch.setattr(data, "provider_cooldown_active_live", lambda _: {"active": False})
    monkeypatch.setattr(data, "reserve_provider_budget_live", lambda *a, **k: {"reserved": True})
    get = Mock(return_value=SimpleNamespace(status_code=200, json=lambda: {"time": "2026-10-31T00:00:00Z"}))
    client = data.PublicResearchData(get)
    for n in range(30):
        client.request("Coinbase Public Research", f"products/fixture{n}-USD/book", {"level": 1})
    snapshot = client.drain_request_diagnostics()
    assert snapshot["rows"] == [{
        "provider": "COINBASE", "endpoint": "L1_BOOK", "cache_hits": 0,
        "cache_misses": 30, "reservation_attempts": 30,
        "reservations_granted": 30, "reservations_denied": 0}]
    assert get.call_count == 30
