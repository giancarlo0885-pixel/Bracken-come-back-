"""Regression coverage for research-only Yahoo fallback throttling."""
import pandas as pd
import pytest

import provider_router as router


@pytest.fixture(autouse=True)
def reset_yahoo_cooldown(monkeypatch):
    monkeypatch.setattr(router, "_provider_cooldowns", {})
    monkeypatch.setattr(router, "mark_provider_cooldown_live", lambda *args, **kwargs: None)
    monkeypatch.setattr(router, "provider_cooldown_active_live", lambda provider: {"active": False})
    monkeypatch.setattr(router, "get_api_settings", lambda: {})
    monkeypatch.setattr(router, "symbol_is_unavailable", lambda *args, **kwargs: False)
    monkeypatch.setattr(router, "mark_symbol_unavailable", lambda *args, **kwargs: None)
    monkeypatch.setattr(router, "record_capability_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(router, "is_in_market_scope", lambda symbol: True)
    monkeypatch.setattr(router, "infer_asset_class", lambda symbol: "stock")
    monkeypatch.setattr(router, "capability_available", lambda *args, **kwargs: False)
    monkeypatch.setattr(router, "_record_failure", lambda *args, **kwargs: None)


def test_yahoo_rate_limit_triggers_cooldown_and_skips_followup():
    calls = []

    def throttled(*args):
        calls.append(args)
        raise RuntimeError("YFRateLimitError: Too Many Requests")

    first = router.route_history("AAPL", "1mo", "1d", throttled)
    assert len(calls) == 1
    assert any(a.provider == "Yahoo Finance" and a.status == "rate_limited" for a in first.attempts)
    second = router.route_history("MSFT", "1mo", "1d", throttled)
    assert len(calls) == 1
    assert any(a.provider == "Yahoo Finance" and a.status == "provider_cooldown" for a in second.attempts)


def test_yahoo_recovers_after_cooldown_expiry(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(router.time, "time", lambda: clock[0])
    calls = []

    def throttled(*args):
        calls.append(args)
        raise RuntimeError("429 rate limit")

    router.route_history("AAPL", "1mo", "1d", throttled)
    clock[0] += router.PROVIDER_RATE_LIMIT_COOLDOWN_SECONDS + 1
    router.route_history("MSFT", "1mo", "1d", throttled)
    assert len(calls) == 2


def test_yahoo_fallback_remains_unverified_research_only():
    frame = pd.DataFrame(
        {"Open": [100.0], "High": [102.0], "Low": [99.0], "Close": [101.0], "Volume": [1000]},
        index=pd.DatetimeIndex(["2026-10-07"], name="Date"),
    )
    result = router.route_history("AAPL", "1mo", "1d", lambda *args: frame)
    assert result.provider == "Yahoo Finance"
    assert result.frame.attrs["quote_verified"] is False
    assert result.frame.attrs["source_mode"] == "strict_research_fallback"
