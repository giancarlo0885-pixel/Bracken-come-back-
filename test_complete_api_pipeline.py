import pandas as pd
import provider_router


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


def test_router_marks_unavailable_symbol_after_empty_routes(monkeypatch):
    provider_router._symbol_cooldowns.clear()
    monkeypatch.setattr(provider_router, "get_api_settings", lambda: type("S", (), {"get": lambda self, n: None})())
    routed = provider_router.route_history("UNI-USD", "1d", "1m", lambda *args: pd.DataFrame())
    assert routed.provider == "none"
    assert provider_router.symbol_is_unavailable("UNI-USD") is True
    skipped = provider_router.route_history("UNI-USD", "1d", "1m", lambda *args: pd.DataFrame())
    assert skipped.attempts[0].status == "symbol_cooldown"


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
