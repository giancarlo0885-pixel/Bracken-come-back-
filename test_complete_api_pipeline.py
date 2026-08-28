import pandas as pd
import provider_router
from cache import make_key, set_value


def test_router_falls_back_and_records_attempts(monkeypatch):
    provider_router._provider_cooldowns.clear()
    provider_router._symbol_cooldowns.clear()
    monkeypatch.setattr(provider_router, "get_api_settings", lambda: type("S", (), {"get": lambda self, n: None})())
    idx = pd.date_range("2026-01-01", periods=3, tz="UTC")
    frame = pd.DataFrame({"Close":[1,2,3], "Volume":[4,5,6]}, index=idx)
    routed = provider_router.route_history("TEST", "1y", "1d", lambda *args: frame)
    assert routed.provider == "Yahoo Finance"
    assert routed.metadata()["records"] == 3
    assert routed.attempts[-1].ok is True
    assert routed.metadata()["quote_verified"] is False
    assert routed.attempts[-1].status == "strict_research_fallback"


def test_router_marks_unavailable_symbol_after_empty_routes(monkeypatch):
    provider_router._symbol_cooldowns.clear()
    monkeypatch.setattr(provider_router, "get_api_settings", lambda: type("S", (), {"get": lambda self, n: None})())
    routed = provider_router.route_history("UNI-USD", "1d", "1m", lambda *args: pd.DataFrame())
    assert routed.provider == "none"
    assert provider_router.symbol_is_unavailable("UNI-USD") is True
    skipped = provider_router.route_history("UNI-USD", "1d", "1m", lambda *args: pd.DataFrame())
    assert skipped.attempts[0].status == "symbol_cooldown"


def test_intraday_symbol_cooldown_does_not_block_daily_history(monkeypatch):
    provider_router._provider_cooldowns.clear()
    provider_router._symbol_cooldowns.clear()

    class Settings:
        def get(self, name):
            return "key" if name == "POLYGON_API_KEY" else None

    def polygon(symbol, period, interval, key):
        if interval == "1m":
            raise RuntimeError("intraday unavailable")
        idx = pd.DatetimeIndex(["2026-01-02"])
        frame = pd.DataFrame({"Close": [10.0], "Volume": [1000]}, index=idx)
        return provider_router._verified_history(frame, "Polygon", symbol, symbol, period, interval, identity_verified=True)

    monkeypatch.setattr(provider_router, "get_api_settings", lambda: Settings())
    monkeypatch.setattr(provider_router, "_polygon", polygon)
    monkeypatch.setattr(provider_router, "reserve_provider_budget_live", lambda *args, **kwargs: {"reserved": True})

    intraday = provider_router.route_history("V39COOL", "1d", "1m", lambda *args: pd.DataFrame())
    assert any(attempt.status == "degraded" for attempt in intraday.attempts)

    daily = provider_router.route_history("V39COOL", "5d", "1d", lambda *args: pd.DataFrame())
    assert daily.provider == "Polygon"
    assert daily.frame.attrs["quote_verified"] is True


def test_cache_hit_consumes_zero_provider_budget(monkeypatch):
    provider_router._provider_cooldowns.clear()
    provider_router._symbol_cooldowns.clear()

    class Settings:
        def get(self, name):
            return "key" if name == "POLYGON_API_KEY" else None

    namespace = "history_polygon_CACHEDV39_5d_1d_adjusted_true_extended_false"
    cache_key = make_key(namespace, "CACHEDV39", "5d", "1d")
    idx = pd.DatetimeIndex(["2026-01-02"])
    frame = pd.DataFrame({"Close": [10.0], "Volume": [1000]}, index=idx)
    cached = provider_router._verified_history(frame, "Polygon", "CACHEDV39", "CACHEDV39", "5d", "1d", identity_verified=True)
    set_value(cache_key, cached, 60)
    budget_calls = []

    monkeypatch.setattr(provider_router, "get_api_settings", lambda: Settings())
    monkeypatch.setattr(provider_router, "_polygon", lambda *args: (_ for _ in ()).throw(AssertionError("external call should not run")))
    monkeypatch.setattr(provider_router, "reserve_provider_budget_live", lambda *args, **kwargs: budget_calls.append(args) or {"reserved": True})

    routed = provider_router.route_history("CACHEDV39", "5d", "1d", lambda *args: pd.DataFrame())

    assert routed.provider == "Polygon"
    assert budget_calls == []


def test_rate_limited_provider_enters_cooldown(monkeypatch):
    provider_router._provider_cooldowns.clear()

    class Settings:
        def get(self, name):
            return "key" if name == "POLYGON_API_KEY" else None

    def limited(*args, **kwargs):
        raise RuntimeError("429 rate limit")

    monkeypatch.setattr(provider_router, "get_api_settings", lambda: Settings())
    monkeypatch.setattr(provider_router, "_polygon", limited)
    routed = provider_router.route_history("AAPL", "1d", "1m", lambda *args: pd.DataFrame())
    assert any(attempt.status == "rate_limited" for attempt in routed.attempts)
    provider_router._symbol_cooldowns.clear()
    second = provider_router.route_history("MSFT", "1d", "1m", lambda *args: pd.DataFrame())
    assert any(attempt.status == "provider_cooldown" for attempt in second.attempts)


def test_provider_failures_are_aggregated_not_logged_per_symbol(monkeypatch):
    provider_router._failure_summary.clear()
    provider_router._provider_cooldowns.clear()
    provider_router._last_failure_log = 0
    messages = []
    monkeypatch.setattr(provider_router.log, "info", lambda message, *args: messages.append(message % args))
    provider_router._record_failure("Polygon", "degraded", "AAA")
    provider_router._record_failure("Polygon", "degraded", "BBB")
    assert len(messages) == 1
    assert "affected_symbols=1" in messages[0]
    assert "History route failed" not in messages[0]
