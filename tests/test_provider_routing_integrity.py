"""Read-only stock routing, authentication cooldown, and cache provenance."""
import pandas as pd
import pytest

import cache
import provider_router as router
from alpaca_iex_provider import AlpacaDataError


@pytest.fixture(autouse=True)
def isolated_router(monkeypatch):
    monkeypatch.setenv("APCA_API_KEY_ID", "unit-alpaca-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "unit-alpaca-secret")
    monkeypatch.setattr(router, "_provider_cooldowns", {})
    monkeypatch.setattr(router, "_symbol_cooldowns", {})
    monkeypatch.setattr(cache, "_cache", {})
    monkeypatch.setattr(router, "get_api_settings", lambda: {})
    monkeypatch.setattr(router, "capability_available", lambda *args: True)
    monkeypatch.setattr(router, "capability_priority_penalty", lambda *args: 0)
    monkeypatch.setattr(router, "record_capability_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(router, "provider_cooldown_active_live", lambda *args: {"active": False})
    monkeypatch.setattr(router, "reserve_provider_budget_live", lambda *args, **kwargs: {"reserved": True})
    monkeypatch.setattr(router, "mark_provider_cooldown_live", lambda *args, **kwargs: None)
    monkeypatch.setattr(router, "_record_failure", lambda *args: None)


def _bars():
    return pd.DataFrame(
        {"Open": [100.0], "High": [102.0], "Low": [99.0], "Close": [101.0], "Volume": [200.0]},
        index=pd.DatetimeIndex(["2026-10-13T15:00:00Z"]),
    )


def _no_yahoo(*args):
    raise AssertionError("Yahoo must not replace available verified stock history")


@pytest.mark.parametrize("symbol", ["MSFT", "GOOG", "GOOGL", "QQQ", "XLK"])
@pytest.mark.parametrize("interval", ["1d", "5m"])
def test_supported_stocks_and_etfs_prefer_alpaca_iex(monkeypatch, symbol, interval):
    called = []
    monkeypatch.setattr(router, "iex_stock_history", lambda *args: called.append(args) or _bars())
    # Another legitimate provider is configured; healthy IEX has first priority.
    monkeypatch.setattr(router, "get_api_settings", lambda: {"POLYGON_API_KEY": "unit-polygon"})
    monkeypatch.setattr(router, "_polygon", lambda *args: (_ for _ in ()).throw(AssertionError("IEX should win")))

    result = router.route_history(symbol, "5d", interval, _no_yahoo)

    assert called == [(symbol, "5d", interval)]
    assert result.provider == result.frame.attrs["provider"] == "Alpaca IEX"
    assert result.frame.attrs["provider_symbol"] == symbol
    assert result.frame.attrs["quote_verified"] is True
    assert result.frame.attrs["adjusted"] is False
    assert result.frame.attrs["extended_hours"] is False
    assert result.frame.attrs["cache_identity"].endswith("adjusted_false_extended_false")


def test_alpaca_401_cools_every_symbol_and_worker_then_recovers(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(router.time, "time", lambda: clock[0])
    shared = {}
    calls = []

    def mark(provider, seconds, reason):
        shared[provider] = {"until": clock[0] + seconds, "reason": reason}

    def active(provider):
        state = shared.get(provider, {})
        return {"active": state.get("until", 0) > clock[0], "reason": state.get("reason")}

    def rejected(*args):
        calls.append(args)
        raise AlpacaDataError("Alpaca market-data HTTP 401")

    monkeypatch.setattr(router, "provider_cooldown_active_live", active)
    monkeypatch.setattr(router, "mark_provider_cooldown_live", mark)
    monkeypatch.setattr(router, "iex_stock_history", rejected)

    first = router.route_history("MSFT", "5d", "5m", lambda *args: _bars())
    assert any(a.provider == "Alpaca IEX" and a.status == "authentication_failed" for a in first.attempts)
    assert first.provider == "Yahoo Finance"
    assert first.frame.attrs["quote_verified"] is False
    assert shared["Alpaca IEX"]["until"] == clock[0] + router.PROVIDER_PERMISSION_COOLDOWN_SECONDS

    # Simulate another worker with independent process-local state.
    monkeypatch.setattr(router, "_provider_cooldowns", {})
    second = router.route_history("GOOG", "5d", "1d", lambda *args: _bars())
    assert len(calls) == 1
    assert any(a.provider == "Alpaca IEX" and a.status == "provider_shared_cooldown" for a in second.attempts)

    clock[0] += router.PROVIDER_PERMISSION_COOLDOWN_SECONDS + 1
    monkeypatch.setattr(router, "iex_stock_history", lambda *args: _bars())
    recovered = router.route_history("GOOGL", "5d", "1d", _no_yahoo)
    assert recovered.provider == "Alpaca IEX"


def test_local_shorter_failure_does_not_reduce_existing_authentication_cooldown(monkeypatch):
    monkeypatch.setattr(router.time, "time", lambda: 1000.0)
    router._mark_provider_limited("Alpaca IEX", router.PROVIDER_PERMISSION_COOLDOWN_SECONDS)
    expected = router._provider_cooldowns["Alpaca IEX"]
    router._mark_provider_limited("Alpaca IEX")
    assert router._provider_cooldowns["Alpaca IEX"] == expected


def test_cache_copy_preserves_original_provider_without_consuming_another_request(monkeypatch):
    calls = []
    monkeypatch.setattr(router, "iex_stock_history", lambda *args: calls.append(args) or _bars())
    first = router.route_history("MSFT", "5d", "5m", _no_yahoo)
    first.frame.attrs["provider"] = "Yahoo Finance"
    first.frame.attrs["quote_verified"] = False

    second = router.route_history("MSFT", "5d", "5m", _no_yahoo)
    assert len(calls) == 1
    assert second.frame.attrs["provider"] == "Alpaca IEX"
    assert second.frame.attrs["quote_verified"] is True


def test_wrong_provider_cache_payload_cannot_be_reported_as_alpaca(monkeypatch):
    namespace = "history_alpaca_iex_MSFT_5d_5m_adjusted_false_extended_false"
    contaminated = router._verified_history(_bars(), "Yahoo Finance", "MSFT", "MSFT", "5d", "5m", identity_verified=True)
    cache.set_value(cache.make_key(namespace, "MSFT", "5d", "5m"), contaminated, 60)
    monkeypatch.setattr(router, "iex_stock_history", lambda *args: (_ for _ in ()).throw(AssertionError("cached payload used")))

    result = router.route_history("MSFT", "5d", "5m", lambda *args: _bars())
    assert result.provider == "Yahoo Finance"
    assert result.frame.attrs["quote_verified"] is False
    assert any(a.provider == "Alpaca IEX" and not a.ok for a in result.attempts)
