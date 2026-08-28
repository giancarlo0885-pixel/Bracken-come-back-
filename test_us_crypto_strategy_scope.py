from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

import asset_routing
import global_market_scanner
import global_pit_engine
import market_sessions
import oracle_bot
import provider_router
import regulatory_monitor
import provider_capabilities


def _history(symbol: str = "AAPL", price: float = 100.0, interval: str = "1d") -> pd.DataFrame:
    index = pd.date_range(datetime.now(timezone.utc) - timedelta(minutes=1), periods=2, freq="min") if interval != "1d" else pd.DatetimeIndex(["2026-07-30", "2026-07-31"])
    return pd.DataFrame({"Close": [price - 1, price], "Volume": [1000, 1200]}, index=index)


def _clear_router(monkeypatch):
    provider_router._provider_cooldowns.clear()
    provider_router._symbol_cooldowns.clear()
    provider_capabilities._cooldowns.clear()
    provider_capabilities._health.clear()
    monkeypatch.setattr(provider_router, "provider_cooldown_active_live", lambda *args, **kwargs: {"active": False})
    monkeypatch.setattr(provider_router, "reserve_provider_budget_live", lambda *args, **kwargs: {"reserved": True})
    monkeypatch.setattr(provider_router, "get_api_settings", lambda: {})
    monkeypatch.setattr(provider_router, "cache_get", lambda *args, **kwargs: None)
    monkeypatch.setattr(provider_router, "cache_set_value", lambda *args, **kwargs: None)


def test_foreign_suffixes_never_enter_stock_provider_routing(monkeypatch):
    _clear_router(monkeypatch)
    called = []
    monkeypatch.setattr(provider_router, "_polygon", lambda *args: called.append("polygon") or pd.DataFrame())

    routed = provider_router.route_history("SHEL.L", "5d", "1d", lambda *args: called.append("yahoo") or _history())

    assert routed.provider == "none"
    assert routed.attempts[0].status == "scope_rejected"
    assert called == []


def test_us_symbols_route_normally(monkeypatch):
    _clear_router(monkeypatch)
    monkeypatch.setattr(provider_router, "get_api_settings", lambda: {"POLYGON_API_KEY": "key"})
    monkeypatch.setattr(
        provider_router,
        "_polygon",
        lambda symbol, period, interval, key: provider_router._verified_history(
            _history(symbol, 101, interval),
            "Polygon",
            symbol,
            symbol,
            period,
            interval,
            identity_verified=True,
        ),
    )

    routed = provider_router.route_history("AAPL", "5d", "1d", lambda *args: pd.DataFrame())

    assert routed.provider == "Polygon"
    assert routed.frame.attrs["requested_symbol"] == "AAPL"
    assert routed.frame.attrs["quote_verified"] is True


def test_strict_yahoo_can_rescue_primary_failure(monkeypatch):
    _clear_router(monkeypatch)

    routed = provider_router.route_history("AAPL", "5d", "1d", lambda *args: _history("AAPL", 150))

    assert routed.provider == "Yahoo Finance"
    assert routed.attempts[-1].status == "strict_research_fallback"
    assert routed.frame.attrs["quote_verified"] is False


def test_yahoo_zero_daily_budget_does_not_disable_strict_research_fallback(monkeypatch):
    _clear_router(monkeypatch)
    monkeypatch.setitem(provider_router.PROVIDER_DAILY_REQUEST_BUDGETS, "yahoo", 0)
    monkeypatch.setitem(provider_router.PROVIDER_CAPABILITY_DAILY_BUDGETS, ("yahoo", "history"), 0)
    budget_calls = []
    monkeypatch.setattr(provider_router, "reserve_provider_budget_live", lambda *args, **kwargs: budget_calls.append(args) or {"reserved": False})

    routed = provider_router.route_history("AAPL", "5d", "1d", lambda *args: _history("AAPL", 150))

    assert routed.provider == "Yahoo Finance"
    assert routed.frame.attrs["quote_verified"] is False
    assert routed.attempts[-1].status == "strict_research_fallback"
    assert budget_calls == []


def test_strict_yahoo_rejects_zero_and_foreign_data(monkeypatch):
    _clear_router(monkeypatch)

    zero = provider_router.route_history("AAPL", "5d", "1d", lambda *args: _history("AAPL", 0))
    foreign = provider_router.route_history("BHP.AX", "5d", "1d", lambda *args: _history("BHP.AX", 100))

    assert zero.provider == "none"
    assert foreign.provider == "none"
    assert foreign.attempts[0].status == "scope_rejected"


def test_stale_and_mismatched_yahoo_quotes_cannot_authorize_execution():
    stale = {
        "symbol": "AAPL",
        "requested_symbol": "AAPL",
        "provider_symbol": "AAPL",
        "provider": "Yahoo Finance",
        "price": 150,
        "quote_verified": True,
        "quote_timestamp": "2020-01-01T14:30:00+00:00",
        "interval": "1m",
    }
    mismatch = {
        **stale,
        "quote_timestamp": datetime.now(timezone.utc).isoformat(),
        "provider_symbol": "MSFT",
    }
    assert oracle_bot._verified_quote_for("AAPL", {"AAPL": stale}, "cash") is None
    assert oracle_bot._verified_quote_for("AAPL", {"AAPL": mismatch}, "cash") is None


def test_crypto_yahoo_mapping_is_strict_research_fallback(monkeypatch):
    _clear_router(monkeypatch)

    routed = provider_router.route_history("BTC-USD", "5d", "1d", lambda *args: _history("BTC-USD", 65000))

    assert routed.provider == "Yahoo Finance"
    assert routed.frame.attrs["requested_symbol"] == "BTC-USD"
    assert routed.frame.attrs["provider_symbol"] == "BTC-USD"
    assert routed.frame.attrs["quote_verified"] is False


def test_google_sanity_cannot_authorize_trade_by_itself():
    quote = {
        "symbol": "AAPL",
        "requested_symbol": "AAPL",
        "provider_symbol": "AAPL",
        "provider": "Google",
        "price": 100.0,
        "quote_verified": False,
        "quote_timestamp": datetime.now(timezone.utc).isoformat(),
        "interval": "1m",
    }
    assert global_pit_engine._execution_quote_eligible(quote) is False


def test_provider_failure_classification_covers_strategy_reasons():
    assert provider_router.classify_provider_failure("429 too many requests") == "rate_limited"
    assert provider_router.classify_provider_failure("402 Payment Required") == "payment_required"
    assert provider_router.classify_provider_failure("premium entitlement required", 403) == "plan_limited"
    assert provider_router.classify_provider_failure("request timeout") == "timeout"
    assert provider_router.classify_provider_failure("symbol mismatch") == "symbol_mismatch"


def test_one_failed_symbol_does_not_stop_scanner_cycle(monkeypatch):
    monkeypatch.setattr(global_market_scanner, "GLOBAL_SCANNER_ENABLED", True)
    monkeypatch.setattr(global_market_scanner, "_ensure_tables", lambda: None)
    monkeypatch.setattr(global_market_scanner, "global_universe", lambda: [
        {"symbol": "BAD", "name": "Bad", "exchange": "US", "region": "United States", "sector": "Technology"},
        {"symbol": "AAPL", "name": "Apple", "exchange": "US", "region": "United States", "sector": "Technology"},
    ])
    monkeypatch.setattr(global_market_scanner, "provider_mover_universe", lambda: [])
    monkeypatch.setattr(global_market_scanner, "_candidate_metrics", lambda meta: (_ for _ in ()).throw(RuntimeError("boom")) if meta["symbol"] == "BAD" else None)

    class Conn:
        def execute(self, *args, **kwargs):
            return self

        def fetchone(self):
            return {"cursor": 0}

        def fetchall(self):
            return []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(global_market_scanner, "connect", lambda: Conn())
    assert global_market_scanner.scan_global_markets() == []


def test_stock_extended_hours_freshness_differs_from_crypto_24_7():
    now = datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)
    stale_intraday = now - timedelta(hours=4)
    assert market_sessions.quote_is_fresh(stale_intraday, "1m", now, symbol="AAPL", max_intraday_age_seconds=120) is False
    assert market_sessions.quote_is_fresh(now - timedelta(seconds=60), "1m", now, symbol="BTC-USD", max_intraday_age_seconds=120) is True


def test_missing_intelligence_reduces_confidence_not_score_positive():
    full = global_pit_engine.strategy_opportunity_score(
        {
            "trend_score": 80,
            "news_score": 80,
            "liquidity_score": 80,
            "regime_score": 80,
            "risk_reward_score": 80,
            "flow_score": 80,
            "confidence": 90,
        }
    )
    missing = global_pit_engine.strategy_opportunity_score({"trend_score": 80, "confidence": 90})
    assert full["opportunity_score"] > missing["opportunity_score"]
    assert full["confidence"] > missing["confidence"]


def test_regulatory_monitor_parses_and_expires_official_events(monkeypatch):
    pub = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")
    xml = f"""<?xml version="1.0"?><rss><channel><item><title>SEC announces crypto ETF update</title><link>https://sec.gov/news</link><pubDate>{pub}</pubDate></item></channel></rss>"""

    class Response:
        text = xml

        def raise_for_status(self):
            return None

    monkeypatch.setattr(regulatory_monitor.requests, "get", lambda *args, **kwargs: Response())
    records = regulatory_monitor._crypto_regulatory_events()
    assert records
    assert records[0]["source"] in {"SEC", "CFTC"}
    assert records[0]["expires_at"] > records[0]["published_at"]


def test_market_scope_default_is_us_crypto():
    assert asset_routing.market_scope() == "US_CRYPTO"
    assert asset_routing.is_in_market_scope("AAPL") is True
    assert asset_routing.is_in_market_scope("SPY") is True
    assert asset_routing.is_in_market_scope("BTC-USD") is True
    assert asset_routing.is_in_market_scope("SAP.DE") is False


def test_provider_capabilities_do_not_advertise_international_history():
    for provider in ("Polygon", "Finnhub", "EODHD", "Alpha Vantage", "Yahoo Finance"):
        assert provider_capabilities.capability_supported(provider, "international_history") is False


def test_provider_capability_health_demotes_weak_route_and_uses_secondary(monkeypatch):
    _clear_router(monkeypatch)
    monkeypatch.setattr(provider_router, "get_api_settings", lambda: {"POLYGON_API_KEY": "key", "EODHD_API_KEY": "key"})
    provider_capabilities.record_capability_result("Polygon", "us_history", False, "timeout")

    calls = []

    def polygon(symbol, period, interval, key):
        calls.append("Polygon")
        return provider_router._verified_history(
            _history(symbol, 101, interval),
            "Polygon",
            symbol,
            symbol,
            period,
            interval,
            identity_verified=True,
        )

    def eodhd(symbol, period, interval, key):
        calls.append("EODHD")
        return provider_router._verified_history(
            _history(symbol, 102, interval),
            "EODHD",
            symbol,
            symbol,
            period,
            interval,
            identity_verified=True,
        )

    monkeypatch.setattr(provider_router, "_polygon", polygon)
    monkeypatch.setattr(provider_router, "_eodhd", eodhd)

    routed = provider_router.route_history("AAPL", "5d", "1d", lambda *args: pd.DataFrame())

    assert routed.provider == "EODHD"
    assert calls == ["EODHD"]
    assert provider_capabilities.capability_health("Polygon", "us_history").status == "degraded"


def test_provider_capability_health_recovers_after_verified_successes(monkeypatch):
    _clear_router(monkeypatch)
    provider_capabilities.record_capability_result("Polygon", "us_history", False, "timeout")

    assert provider_capabilities.capability_priority_penalty("Polygon", "us_history") > 0

    provider_capabilities.record_capability_result("Polygon", "us_history", True)
    provider_capabilities.record_capability_result("Polygon", "us_history", True)

    assert provider_capabilities.capability_health("Polygon", "us_history").status == "healthy"
    assert provider_capabilities.capability_priority_penalty("Polygon", "us_history") == 0


def test_provider_health_is_capability_specific_not_provider_wide(monkeypatch):
    _clear_router(monkeypatch)
    monkeypatch.setattr(provider_router, "get_api_settings", lambda: {"POLYGON_API_KEY": "key", "EODHD_API_KEY": "key"})
    provider_capabilities.record_capability_result("Polygon", "movers", False, "rate_limited")
    calls = []

    monkeypatch.setattr(
        provider_router,
        "_polygon",
        lambda symbol, period, interval, key: calls.append("Polygon") or provider_router._verified_history(
            _history(symbol, 103, interval),
            "Polygon",
            symbol,
            symbol,
            period,
            interval,
            identity_verified=True,
        ),
    )

    routed = provider_router.route_history("MSFT", "5d", "1d", lambda *args: pd.DataFrame())

    assert routed.provider == "Polygon"
    assert calls == ["Polygon"]
    assert provider_capabilities.capability_health("Polygon", "movers").status == "last_resort"
    assert provider_capabilities.capability_health("Polygon", "us_history").status == "healthy"


def test_unverified_primary_provider_cannot_bypass_quote_verification(monkeypatch):
    _clear_router(monkeypatch)
    monkeypatch.setattr(provider_router, "get_api_settings", lambda: {"POLYGON_API_KEY": "key", "EODHD_API_KEY": "key"})

    def unverified(symbol, period, interval, key):
        frame = _history(symbol, 104, interval)
        frame.attrs.update(
            {
                "requested_symbol": symbol,
                "provider_symbol": symbol,
                "provider": "Polygon",
                "quote_verified": False,
            }
        )
        return frame

    def verified(symbol, period, interval, key):
        return provider_router._verified_history(
            _history(symbol, 105, interval),
            "EODHD",
            symbol,
            symbol,
            period,
            interval,
            identity_verified=True,
        )

    monkeypatch.setattr(provider_router, "_polygon", unverified)
    monkeypatch.setattr(provider_router, "_eodhd", verified)

    routed = provider_router.route_history("GOOGL", "5d", "1d", lambda *args: pd.DataFrame())

    assert routed.provider == "EODHD"
    assert [attempt.provider for attempt in routed.attempts[:2]] == ["Polygon", "EODHD"]
    assert routed.frame.attrs["quote_verified"] is True
