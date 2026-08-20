from __future__ import annotations

import uuid
from contextlib import contextmanager

import pandas as pd
import pytest
import requests

import alpha_vantage_provider as av
import market_data
import provider_diagnostics
import provider_router
from provider_router import _redact_url, route_history


class Response:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def alpha_env(monkeypatch):
    av.reset_state_for_tests()
    provider_router._symbol_cooldowns.clear()
    provider_router._provider_cooldowns.clear()
    monkeypatch.setattr(av, "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS", 0)
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "secret-alpha-key")
    yield
    av.reset_state_for_tests()
    provider_router._symbol_cooldowns.clear()
    provider_router._provider_cooldowns.clear()


def test_successful_alpha_vantage_global_quote_response(monkeypatch):
    symbol = f"T{uuid.uuid4().hex[:6]}".upper()

    def fake_get(url, params=None, timeout=20):
        assert params["apikey"] == "secret-alpha-key"
        return Response(
            {
                "Global Quote": {
                    "01. symbol": symbol,
                    "05. price": "188.50",
                    "06. volume": "1234567",
                    "07. latest trading day": "2026-08-19",
                    "08. previous close": "180.00",
                    "10. change percent": "4.7222%",
                }
            }
        )

    monkeypatch.setattr(av.requests, "get", fake_get)
    quote = av.global_quote(symbol)

    assert quote["provider"] == "Alpha Vantage"
    assert quote["price"] == 188.50
    assert quote["requested_symbol"] == symbol
    assert quote["provider_symbol"] == symbol


def test_alpha_vantage_rate_limit_enters_cooldown(monkeypatch):
    def fake_get(url, params=None, timeout=20):
        return Response({"Note": "Our standard API call frequency is 5 calls per minute."})

    monkeypatch.setattr(av.requests, "get", fake_get)

    with pytest.raises(RuntimeError, match="rate_limited"):
        av.global_quote("IBM")
    assert av.cooldown_remaining_seconds() > 0


def test_alpha_vantage_invalid_symbol_returns_no_quote(monkeypatch):
    def fake_get(url, params=None, timeout=20):
        return Response({"Global Quote": {"01. symbol": "MSFT", "05. price": "100"}})

    monkeypatch.setattr(av.requests, "get", fake_get)
    assert av.global_quote("AAPL") is None


def test_alpha_vantage_eod_quote_is_not_marked_realtime(monkeypatch):
    symbol = f"EOD{uuid.uuid4().hex[:4]}".upper()

    def fake_get(url, params=None, timeout=20):
        return Response({"Global Quote": {"01. symbol": symbol, "05. price": "10", "07. latest trading day": "2026-08-19"}})

    monkeypatch.setattr(av.requests, "get", fake_get)
    snapshot = market_data._alpha_vantage_delayed_snapshot(symbol)

    assert snapshot is not None
    assert snapshot.provider == "Alpha Vantage"
    assert snapshot.quote_verified is False
    assert snapshot.interval == "1d"


def test_alpha_vantage_global_quote_fallback_does_not_replace_fresher_verified_provider(monkeypatch):
    verified = market_data.MarketSnapshot(
        symbol="AAPL",
        price=200,
        change_pct=1,
        volume=1000,
        timestamp="2026-08-19T14:30:00+00:00",
        provider="Polygon",
        interval="1m",
        requested_symbol="AAPL",
        provider_symbol="AAPL",
        quote_verified=True,
    )

    monkeypatch.setattr(market_data, "_snapshot_from_history", lambda symbol, history, interval: verified)
    monkeypatch.setattr(market_data, "get_history", lambda symbol, period, interval: pd.DataFrame({"Close": [200]}))
    monkeypatch.setattr(market_data, "_alpha_vantage_delayed_snapshot", lambda symbol: pytest.fail("Alpha fallback should not run"))

    assert market_data.get_live_snapshot("AAPL") is verified


def test_alpha_vantage_global_quote_fallback_routing(monkeypatch):
    monkeypatch.setattr(market_data, "get_history", lambda symbol, period, interval: pd.DataFrame())
    monkeypatch.setattr(market_data, "_alpha_vantage_delayed_snapshot", lambda symbol: market_data.MarketSnapshot(
        symbol="F",
        price=12,
        change_pct=0,
        volume=100,
        timestamp="2026-08-19",
        provider="Alpha Vantage",
        interval="1d",
        requested_symbol="F",
        provider_symbol="F",
        quote_verified=False,
    ))

    snapshot = market_data.get_live_snapshot("F")
    assert snapshot.provider == "Alpha Vantage"
    assert snapshot.quote_verified is False


def test_alpha_vantage_api_key_redaction():
    text = "https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=IBM&apikey=secret-alpha-key"
    redacted = _redact_url(text)

    assert "secret-alpha-key" not in redacted
    assert "apikey=REDACTED" in redacted


def test_alpha_vantage_http_error_never_leaks_api_key(monkeypatch):
    class BadResponse:
        status_code = 403
        text = "forbidden"
        url = "https://www.alphavantage.co/query?function=GLOBAL_QUOTE&apikey=secret-alpha-key"

        def raise_for_status(self):
            raise requests.HTTPError(f"403 Client Error for url: {self.url}")

        def json(self):
            return {}

    monkeypatch.setattr(av.requests, "get", lambda *args, **kwargs: BadResponse())

    with pytest.raises(RuntimeError) as excinfo:
        av.global_quote("IBM")

    assert "secret-alpha-key" not in str(excinfo.value)
    assert "apikey=REDACTED" in str(excinfo.value)
    health = av.health_probe(probe=False)
    assert "secret-alpha-key" not in str(health.last_error)
    diagnostics = provider_diagnostics.diagnose_provider("ALPHA_VANTAGE_API_KEY", force=True)
    assert "secret-alpha-key" not in str(diagnostics.last_error)
    assert "secret-alpha-key" not in diagnostics.message


def test_alpha_vantage_request_cache_reuses_response(monkeypatch):
    symbol = f"C{uuid.uuid4().hex[:6]}".upper()
    calls = {"count": 0}

    def fake_get(url, params=None, timeout=20):
        calls["count"] += 1
        return Response({"Global Quote": {"01. symbol": symbol, "05. price": "101"}})

    monkeypatch.setattr(av.requests, "get", fake_get)

    assert av.global_quote(symbol)["price"] == 101
    assert av.global_quote(symbol)["price"] == 101
    assert calls["count"] == 1


def test_alpha_vantage_shared_daily_quota_blocks_external_request(monkeypatch):
    monkeypatch.setattr(av, "ALPHA_VANTAGE_DAILY_REQUEST_BUDGET", 1)
    calls = {"count": 0}

    def fake_get(url, params=None, timeout=20):
        calls["count"] += 1
        symbol = params["symbol"]
        return Response({"Global Quote": {"01. symbol": symbol, "05. price": "10"}})

    monkeypatch.setattr(av.requests, "get", fake_get)

    assert av.global_quote("ONE")["price"] == 10
    with pytest.raises(RuntimeError, match="quota_exhausted"):
        av.global_quote("TWO")
    assert calls["count"] == 1
    usage = av.usage_snapshot()
    assert usage["requests_used"] == 1
    assert usage["daily_remaining"] == 0


def test_alpha_vantage_daily_quota_uses_shared_database_ledger(monkeypatch):
    monkeypatch.setattr(av, "ALPHA_VANTAGE_DAILY_REQUEST_BUDGET", 1)
    ledger = {}
    calls = {"count": 0}

    class Result:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class Conn:
        def execute(self, sql, params=()):
            normalized = " ".join(sql.split()).lower()
            key = ("Alpha Vantage", av._today())
            if normalized.startswith("select"):
                return Result(ledger.get(key))
            if normalized.startswith("insert into provider_daily_usage") and "returning" in normalized:
                ledger.setdefault(key, {"requests_used": 0, "daily_budget": params[2], "last_request_at": None})
                ledger[key]["daily_budget"] = params[2]
                return Result(dict(ledger[key]))
            if normalized.startswith("update provider_daily_usage set requests_used"):
                ledger[key]["requests_used"] += 1
                ledger[key]["daily_budget"] = params[0]
                ledger[key]["last_request_at"] = params[1]
                return Result()
            if normalized.startswith("update provider_daily_usage set last_error"):
                ledger.setdefault(key, {"requests_used": 0, "daily_budget": 1})
                ledger[key]["last_error"] = params[0]
                return Result()
            if normalized.startswith("insert into provider_daily_usage"):
                ledger.setdefault(key, {"requests_used": 0, "daily_budget": params[2]})
                ledger[key]["last_success"] = params[4]
                ledger[key]["last_error"] = params[5]
                return Result()
            return Result()

    @contextmanager
    def fake_connect():
        yield Conn()

    def fake_get(url, params=None, timeout=20):
        calls["count"] += 1
        symbol = params["symbol"]
        return Response({"Global Quote": {"01. symbol": symbol, "05. price": "10"}})

    import database

    monkeypatch.setattr(database, "connect", fake_connect)
    monkeypatch.setattr(av.requests, "get", fake_get)

    assert av.global_quote("DBONE")["price"] == 10
    with pytest.raises(RuntimeError, match="quota_exhausted"):
        av.global_quote("DBTWO")
    assert calls["count"] == 1
    assert ledger[("Alpha Vantage", av._today())]["requests_used"] == 1


def test_alpha_vantage_symbol_search_discovers_international_metadata(monkeypatch):
    def fake_get(url, params=None, timeout=20):
        return Response({"bestMatches": [{"1. symbol": "VOD.LON", "2. name": "Vodafone", "3. type": "Equity", "4. region": "United Kingdom", "7. timezone": "Europe/London", "8. currency": "GBX"}]})

    monkeypatch.setattr(av.requests, "get", fake_get)
    matches = av.symbol_search("Vodafone")

    assert matches[0]["symbol"] == "VOD.LON"
    assert matches[0]["region"] == "United Kingdom"


def test_alpha_vantage_time_series_daily_history(monkeypatch):
    symbol = f"D{uuid.uuid4().hex[:6]}".upper()

    def fake_get(url, params=None, timeout=20):
        return Response({"Meta Data": {"2. Symbol": symbol}, "Time Series (Daily)": {"2026-08-19": {"1. open": "1", "2. high": "3", "3. low": "1", "4. close": "2", "5. volume": "100"}}})

    monkeypatch.setattr(av.requests, "get", fake_get)
    frame = av.daily_history(symbol)

    assert list(frame.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert frame.attrs["quote_verified"] is True
    assert str(frame.index[-1].date()) == "2026-08-19"


def test_alpha_vantage_market_status(monkeypatch):
    def fake_get(url, params=None, timeout=20):
        return Response({"markets": [{"market_type": "Equity", "region": "United States", "current_status": "open"}]})

    monkeypatch.setattr(av.requests, "get", fake_get)
    assert av.market_status()[0]["current_status"] == "open"


def test_alpha_vantage_movers_are_discovery_only(monkeypatch):
    def fake_get(url, params=None, timeout=20):
        return Response({"top_gainers": [{"ticker": "IBM", "price": "100", "change_percentage": "5%", "volume": "1000"}]})

    monkeypatch.setattr(av.requests, "get", fake_get)
    movers = av.top_gainers_losers()

    assert movers[0]["symbol"] == "IBM"
    assert movers[0]["quote_verified"] is False
    assert "Realtime" not in movers[0]["mode"]


def test_alpha_vantage_news_sentiment(monkeypatch):
    def fake_get(url, params=None, timeout=20):
        return Response({"feed": [{"title": "Markets rise", "summary": "Stocks gained", "source": "Example", "url": "https://example.com/a", "overall_sentiment_label": "Bullish"}]})

    monkeypatch.setattr(av.requests, "get", fake_get)
    records = av.news_sentiment()

    assert records[0]["provider"] == "Alpha Vantage"
    assert records[0]["sentiment"] == "Bullish"


def test_alpha_vantage_fundamentals_are_cached(monkeypatch):
    symbol = f"F{uuid.uuid4().hex[:6]}".upper()
    calls = {"count": 0}

    def fake_get(url, params=None, timeout=20):
        calls["count"] += 1
        return Response({"Symbol": symbol, "Name": "Example Corp"})

    monkeypatch.setattr(av.requests, "get", fake_get)

    assert av.fundamentals(symbol, "OVERVIEW")["provider"] == "Alpha Vantage"
    assert av.fundamentals(symbol, "OVERVIEW")["Name"] == "Example Corp"
    assert calls["count"] == 1


def test_alpha_vantage_is_not_used_for_crypto_history(monkeypatch):
    monkeypatch.setenv("POLYGON_API_KEY", "")
    monkeypatch.setenv("FINNHUB_API_KEY", "")
    monkeypatch.setenv("EODHD_API_KEY", "")
    called = {"alpha": False}

    def alpha_loader(symbol, period, interval, key):
        called["alpha"] = True
        return pd.DataFrame({"Close": [1.0]}, index=pd.DatetimeIndex(["2026-08-19"]))

    monkeypatch.setattr("provider_router._alpha", alpha_loader)
    routed = route_history("BTC-USD", "5d", "1d", lambda symbol, period, interval: pd.DataFrame())

    assert routed.frame.empty
    assert called["alpha"] is False


def test_free_mode_never_calls_alpha_intraday(monkeypatch):
    monkeypatch.setattr(provider_router, "ALPHA_VANTAGE_PREMIUM", False)
    monkeypatch.setattr(provider_router.requests, "get", lambda *args, **kwargs: pytest.fail("free mode must not call premium intraday"))

    assert provider_router._alpha("AAPL", "1d", "5m", "key").empty


def test_free_mode_daily_route_uses_time_series_daily(monkeypatch):
    monkeypatch.setattr(provider_router, "ALPHA_VANTAGE_PREMIUM", False)
    seen = {"function": None}

    def fake_get(url, params=None, timeout=20):
        seen["function"] = params["function"]
        assert params["function"] != "TIME_SERIES_DAILY_ADJUSTED"
        return Response({"Meta Data": {"2. Symbol": "AAPL"}, "Time Series (Daily)": {"2026-08-19": {"1. open": "1", "2. high": "2", "3. low": "1", "4. close": "2", "5. volume": "100"}}})

    monkeypatch.setattr(av.requests, "get", fake_get)

    frame = provider_router._alpha("AAPL", "5d", "1d", "key")
    assert seen["function"] == "TIME_SERIES_DAILY"
    assert not frame.empty


def test_premium_flag_allows_alpha_intraday_route(monkeypatch):
    monkeypatch.setattr(provider_router, "ALPHA_VANTAGE_PREMIUM", True)
    seen = {"function": None}

    def fake_get(url, params=None, timeout=15):
        seen["function"] = params["function"]
        return Response({
            "Meta Data": {"2. Symbol": "AAPL", "6. Time Zone": "America/New_York"},
            "Time Series (5min)": {"2026-08-19 10:00:00": {"1. open": "1", "2. high": "2", "3. low": "1", "4. close": "2", "5. volume": "100"}},
        })

    monkeypatch.setattr(provider_router.requests, "get", fake_get)

    frame = provider_router._alpha("AAPL", "1d", "5m", "key")
    assert seen["function"] == "TIME_SERIES_INTRADAY"
    assert not frame.empty


def test_dashboard_diagnostics_do_not_probe_alpha_vantage(monkeypatch):
    monkeypatch.setattr(av.requests, "get", lambda *args, **kwargs: pytest.fail("diagnostics should read ledger/cache, not call Alpha"))

    result = provider_diagnostics.diagnose_provider("ALPHA_VANTAGE_API_KEY", force=True)
    assert result.provider == "Alpha Vantage"
    assert result.status in {"configured", "healthy"}
    assert result.requests == 0
