from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

import market_data
import market_worker
import oracle_bot
import provider_router
import cache
import provider_capabilities


class _Settings:
    def __init__(self, **values):
        self.values = values

    def get(self, name):
        return self.values.get(name)


class _Response:
    def __init__(self, payload, status_code=200, url="https://provider.test/path"):
        self._payload = payload
        self.status_code = status_code
        self.url = url

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"{self.status_code} provider error {self.url}")


def _ohlcv_frame(price=100.0, interval="1d"):
    index = pd.date_range("2026-01-01", periods=2, tz="UTC") if interval != "1d" else pd.DatetimeIndex(["2026-01-01", "2026-01-02"])
    return pd.DataFrame(
        {
            "Open": [price - 2, price - 1],
            "High": [price + 1, price + 2],
            "Low": [price - 3, price - 2],
            "Close": [price - 1, price],
            "Volume": [500000, 600000],
        },
        index=index,
    )


def _reset_provider_state(monkeypatch):
    provider_router._provider_cooldowns.clear()
    provider_router._symbol_cooldowns.clear()
    provider_router._failure_summary.clear()
    provider_capabilities._cooldowns.clear()
    cache._cache.clear()
    monkeypatch.setattr(provider_router, "provider_cooldown_active_live", lambda *args, **kwargs: {"active": False})
    monkeypatch.setattr(provider_router, "reserve_provider_budget_live", lambda *args, **kwargs: {"reserved": True})


def test_crypto_symbol_adapters_preserve_canonical_identity():
    assert provider_router.polygon_crypto_native_symbol("BTC-USD") == "X:BTCUSD"
    assert provider_router.eodhd_crypto_native_symbol("ETH-USD") == "ETH-USD.CC"
    assert provider_router.native_crypto_to_canonical("Polygon", "X:SOLUSD") == "SOL-USD"
    assert provider_router.native_crypto_to_canonical("EODHD", "BTC-USD.CC") == "BTC-USD"
    assert provider_router.native_crypto_to_canonical("Finnhub", "BINANCE:BTCUSDT") == "BTC-USD"


def test_polygon_crypto_history_uses_native_alias_but_returns_canonical(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        return _Response({"results": [{"t": 1782864000000, "o": 99, "h": 101, "l": 98, "c": 100, "v": 1000}]})

    monkeypatch.setattr(provider_router.requests, "get", fake_get)
    frame = provider_router._polygon("BTC-USD", "1d", "1m", "secret")

    assert "X:BTCUSD" in calls[0][0]
    assert frame.attrs["requested_symbol"] == "BTC-USD"
    assert frame.attrs["provider_symbol"] == "BTC-USD"
    assert frame.attrs["provider_native_symbol"] == "X:BTCUSD"
    assert frame.attrs["quote_verified"] is True


def test_eodhd_crypto_history_uses_cc_exchange_not_us_symbol_list(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        return _Response([{"date": "2026-07-01", "open": 99, "high": 101, "low": 98, "close": 100, "volume": 1000}])

    monkeypatch.setattr(provider_router.requests, "get", fake_get)
    frame = provider_router._eodhd("BTC-USD", "5d", "1d", "secret")

    assert any("/api/eod/BTC-USD.CC" in url for url in calls)
    assert not any("exchange-symbol-list/US" in url for url in calls)
    assert frame.attrs["requested_symbol"] == "BTC-USD"
    assert frame.attrs["provider_symbol"] == "BTC-USD"
    assert frame.attrs["provider_native_symbol"] == "BTC-USD.CC"


def test_finnhub_crypto_uses_crypto_symbol_discovery_and_crypto_candle(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params or {}))
        if url.endswith("/crypto/symbol"):
            return _Response([{"symbol": "BINANCE:BTCUSDT", "displaySymbol": "BTC/USDT"}])
        return _Response({"s": "ok", "t": [1782864000], "o": [99], "h": [101], "l": [98], "c": [100], "v": [1000]})

    monkeypatch.setattr(provider_router.requests, "get", fake_get)
    frame = provider_router._finnhub("BTC-USD", "1d", "1m", "secret")

    assert any(url.endswith("/crypto/symbol") for url, _ in calls)
    assert any(url.endswith("/crypto/candle") and params["symbol"] == "BINANCE:BTCUSDT" for url, params in calls)
    assert not any(url.endswith("/stock/candle") for url, _ in calls)
    assert frame.attrs["provider_symbol"] == "BTC-USD"
    assert frame.attrs["provider_native_symbol"] == "BINANCE:BTCUSDT"


def test_crypto_routes_are_built_from_configured_capabilities(monkeypatch):
    _reset_provider_state(monkeypatch)
    monkeypatch.setitem(provider_router.PROVIDER_CAPABILITY_DAILY_BUDGETS, ("polygon", "crypto"), 10)
    monkeypatch.setitem(provider_router.PROVIDER_CAPABILITY_DAILY_BUDGETS, ("finnhub", "crypto"), 0)
    monkeypatch.setitem(provider_router.PROVIDER_CAPABILITY_DAILY_BUDGETS, ("eodhd", "crypto"), 0)
    monkeypatch.setattr(provider_router, "get_api_settings", lambda: _Settings(POLYGON_API_KEY="key", FINNHUB_API_KEY="key", EODHD_API_KEY="key"))

    def polygon(symbol, period, interval, key):
        return provider_router._verified_history(
            _ohlcv_frame(100, interval),
            "Polygon",
            "BTC-USD",
            "BTC-USD",
            period,
            interval,
            identity_verified=True,
            provider_native_symbol="X:BTCUSD",
        )

    monkeypatch.setattr(provider_router, "_polygon", polygon)
    monkeypatch.setattr(provider_router, "_finnhub", lambda *args: (_ for _ in ()).throw(AssertionError("Finnhub crypto disabled")))
    monkeypatch.setattr(provider_router, "_eodhd", lambda *args: (_ for _ in ()).throw(AssertionError("EODHD crypto disabled")))

    routed = provider_router.route_history("BTC-USD", "1d", "1m", lambda *args: pd.DataFrame())

    assert routed.provider == "Polygon"
    assert routed.frame.attrs["provider_native_symbol"] == "X:BTCUSD"


def test_yahoo_crypto_fallback_is_strict_research(monkeypatch):
    _reset_provider_state(monkeypatch)
    monkeypatch.setitem(provider_router.PROVIDER_CAPABILITY_DAILY_BUDGETS, ("polygon", "crypto"), 0)
    monkeypatch.setitem(provider_router.PROVIDER_CAPABILITY_DAILY_BUDGETS, ("finnhub", "crypto"), 0)
    monkeypatch.setitem(provider_router.PROVIDER_CAPABILITY_DAILY_BUDGETS, ("eodhd", "crypto"), 0)
    monkeypatch.setattr(provider_router, "get_api_settings", lambda: _Settings())

    routed = provider_router.route_history("BTC-USD", "5d", "1d", lambda *args: _ohlcv_frame(100))

    assert routed.provider == "Yahoo Finance"
    assert routed.frame.attrs["quote_verified"] is False
    assert routed.metadata()["quote_verified"] is False
    assert routed.attempts[-1].status == "strict_research_fallback"


def test_polygon_rate_limit_falls_back_to_second_verified_crypto_provider(monkeypatch):
    _reset_provider_state(monkeypatch)
    marked = []
    monkeypatch.setitem(provider_router.PROVIDER_CAPABILITY_DAILY_BUDGETS, ("polygon", "crypto"), 10)
    monkeypatch.setitem(provider_router.PROVIDER_CAPABILITY_DAILY_BUDGETS, ("eodhd", "crypto"), 10)
    monkeypatch.setattr(provider_router, "get_api_settings", lambda: _Settings(POLYGON_API_KEY="key", EODHD_API_KEY="key"))
    monkeypatch.setattr(provider_router, "mark_provider_cooldown_live", lambda provider, **kwargs: marked.append((provider, kwargs)) or {"active": True})
    monkeypatch.setattr(provider_router, "_polygon", lambda *args: (_ for _ in ()).throw(RuntimeError("429 rate limit")))
    monkeypatch.setattr(
        provider_router,
        "_eodhd",
        lambda symbol, period, interval, key: provider_router._verified_history(
            _ohlcv_frame(101, interval),
            "EODHD",
            "BTC-USD",
            "BTC-USD",
            period,
            interval,
            identity_verified=True,
            provider_native_symbol="BTC-USD.CC",
        ),
    )

    routed = provider_router.route_history("BTC-USD", "5d", "1d", lambda *args: pd.DataFrame())

    assert routed.provider == "EODHD"
    assert any(attempt.provider == "Polygon" and attempt.status == "rate_limited" for attempt in routed.attempts)
    assert marked and marked[0][0] == "Polygon"


def test_all_verified_crypto_providers_unavailable_fails_closed(monkeypatch):
    _reset_provider_state(monkeypatch)
    monkeypatch.setitem(provider_router.PROVIDER_CAPABILITY_DAILY_BUDGETS, ("polygon", "crypto"), 0)
    monkeypatch.setitem(provider_router.PROVIDER_CAPABILITY_DAILY_BUDGETS, ("finnhub", "crypto"), 0)
    monkeypatch.setitem(provider_router.PROVIDER_CAPABILITY_DAILY_BUDGETS, ("eodhd", "crypto"), 0)
    monkeypatch.setattr(provider_router, "get_api_settings", lambda: _Settings())

    routed = provider_router.route_history("BTC-USD", "5d", "1d", lambda *args: pd.DataFrame())

    assert routed.provider == "none"
    assert routed.frame.empty


def test_shared_provider_cooldown_blocks_stock_and_crypto_routes(monkeypatch):
    _reset_provider_state(monkeypatch)
    monkeypatch.setitem(provider_router.PROVIDER_CAPABILITY_DAILY_BUDGETS, ("polygon", "crypto"), 10)
    monkeypatch.setattr(provider_router, "get_api_settings", lambda: _Settings(POLYGON_API_KEY="key"))
    monkeypatch.setattr(provider_router, "provider_cooldown_active_live", lambda provider, *args, **kwargs: {"active": provider == "Polygon", "reason": "shared 429 cooldown"})
    monkeypatch.setattr(provider_router, "_polygon", lambda *args: (_ for _ in ()).throw(AssertionError("cooldown should block external request")))

    crypto = provider_router.route_history("BTC-USD", "1d", "1m", lambda *args: pd.DataFrame())
    stock = provider_router.route_history("AAPL", "1d", "1m", lambda *args: pd.DataFrame())

    assert any(attempt.provider == "Polygon" and attempt.status == "provider_shared_cooldown" for attempt in crypto.attempts)
    assert any(attempt.provider == "Polygon" and attempt.status == "provider_shared_cooldown" for attempt in stock.attempts)


def test_crypto_redundancy_reports_degraded_and_unavailable(monkeypatch):
    monkeypatch.setitem(provider_router.PROVIDER_CAPABILITY_DAILY_BUDGETS, ("polygon", "crypto"), 10)
    monkeypatch.setitem(provider_router.PROVIDER_CAPABILITY_DAILY_BUDGETS, ("finnhub", "crypto"), 0)
    monkeypatch.setitem(provider_router.PROVIDER_CAPABILITY_DAILY_BUDGETS, ("eodhd", "crypto"), 0)
    assert provider_router.crypto_execution_provider_redundancy(_Settings(POLYGON_API_KEY="key"))["status"] == "DEGRADED"
    monkeypatch.setitem(provider_router.PROVIDER_CAPABILITY_DAILY_BUDGETS, ("polygon", "crypto"), 0)
    assert provider_router.crypto_execution_provider_redundancy(_Settings(POLYGON_API_KEY="key"))["status"] == "UNAVAILABLE"


def test_unverified_research_price_is_not_execution_quote(monkeypatch):
    _reset_provider_state(monkeypatch)
    monkeypatch.setitem(provider_router.PROVIDER_CAPABILITY_DAILY_BUDGETS, ("polygon", "crypto"), 0)
    monkeypatch.setattr(provider_router, "get_api_settings", lambda: _Settings())
    history = provider_router.route_history("BTC-USD", "5d", "1d", lambda *args: _ohlcv_frame(100)).frame
    history.attrs["quote_verified"] = False
    history.attrs["provider_route"] = provider_router.RoutedHistory(history, "Unverified Research", [], datetime.now(timezone.utc).isoformat()).metadata()

    quote = market_worker._quote_payload_from_history("BTC-USD", history, 100.0, scan_type="fast")

    assert quote["price"] == 100.0
    assert quote["quote_verified"] is False
    assert oracle_bot._verified_quote_for("BTC-USD", {"BTC-USD": quote}, "crypto") is None


def test_btc_execution_handoff_keeps_price_verified_and_native_alias(monkeypatch):
    _reset_provider_state(monkeypatch)
    monkeypatch.setitem(provider_router.PROVIDER_CAPABILITY_DAILY_BUDGETS, ("polygon", "crypto"), 10)
    monkeypatch.setattr(provider_router, "get_api_settings", lambda: _Settings(POLYGON_API_KEY="key"))
    now = datetime.now(timezone.utc).isoformat()

    def polygon(symbol, period, interval, key):
        frame = provider_router._verified_history(
            _ohlcv_frame(62300, interval),
            "Polygon",
            "BTC-USD",
            "BTC-USD",
            period,
            interval,
            identity_verified=True,
            provider_native_symbol="X:BTCUSD",
        )
        frame.attrs["quote_timestamp"] = now
        return frame

    monkeypatch.setattr(provider_router, "_polygon", polygon)

    history = market_data.get_history("BTC-USD", "1d", "1m")
    quote = market_worker._quote_payload_from_history("BTC-USD", history, scan_type="fast")
    verified = oracle_bot._verified_quote_for("BTC-USD", {"BTC-USD": quote}, "crypto")

    assert quote["price"] and quote["price"] > 0
    assert quote["requested_symbol"] == "BTC-USD"
    assert quote["provider_symbol"] == "BTC-USD"
    assert quote["provider_native_symbol"] == "X:BTCUSD"
    assert verified is not None


def test_wrong_provider_identity_is_rejected():
    now = datetime.now(timezone.utc).isoformat()
    quote = {
        "symbol": "BTC-USD",
        "requested_symbol": "BTC-USD",
        "provider_symbol": "ETH-USD",
        "provider_native_symbol": "X:ETHUSD",
        "price": 62300,
        "quote_timestamp": now,
        "interval": "1m",
        "quote_verified": True,
    }

    assert oracle_bot._verified_quote_for("BTC-USD", {"BTC-USD": quote}, "crypto") is None
