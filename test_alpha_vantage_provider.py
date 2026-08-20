from __future__ import annotations

import uuid

import pandas as pd
import pytest

import alpha_vantage_provider as av
import market_data
import provider_diagnostics
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
    monkeypatch.setattr(av, "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS", 0)
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "secret-alpha-key")
    yield
    av.reset_state_for_tests()


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
