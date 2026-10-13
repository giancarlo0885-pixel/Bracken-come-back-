"""Yahoo runtime failures must reach the router's cross-worker cooldown."""
from types import SimpleNamespace

import pandas as pd
import pytest

import provider_router as router
import runtime_provider_reliability as reliability


class YFRateLimitError(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def reset_provider_runtime(monkeypatch):
    monkeypatch.setattr(reliability, "_UNAVAILABLE_UNTIL", {})
    monkeypatch.setattr(reliability, "_KEY_LOCKS", {})
    monkeypatch.setattr(router, "_provider_cooldowns", {})
    monkeypatch.setattr(router, "get_api_settings", lambda: {})
    monkeypatch.setattr(router, "symbol_is_unavailable", lambda *args, **kwargs: False)
    monkeypatch.setattr(router, "mark_symbol_unavailable", lambda *args, **kwargs: None)
    monkeypatch.setattr(router, "capability_available", lambda *args: False)
    monkeypatch.setattr(router, "record_capability_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(router, "_record_failure", lambda *args: None)


def _module(history):
    return SimpleNamespace(
        yf=SimpleNamespace(Ticker=lambda symbol: SimpleNamespace(history=history)),
        _normalize=lambda frame, symbol: frame.copy(),
        _stamp_history=lambda frame, *args: frame.copy(),
    )


@pytest.mark.parametrize("error", [YFRateLimitError("throttled"), RuntimeError("YFRateLimitError"), RuntimeError("HTTP 429")])
def test_runtime_rate_limit_propagates_with_per_key_request_suppression(error):
    calls = []

    def history(**kwargs):
        assert kwargs["raise_errors"] is True
        calls.append(kwargs)
        raise error

    module = _module(history)
    with pytest.raises(type(error)):
        reliability.yahoo_reference_history(module, "MSFT", "5d", "5m")
    assert router.classify_provider_failure(error) == "rate_limited"
    assert reliability.yahoo_reference_history(module, "MSFT", "5d", "5m").empty
    assert len(calls) == 1


def test_runtime_yahoo429_blocks_another_worker_and_recovers_after_shared_expiry(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(router.time, "time", lambda: clock[0])
    shared = {}
    calls = []

    def mark(provider, seconds, reason):
        shared[provider] = clock[0] + seconds

    def active(provider):
        return {"active": shared.get(provider, 0) > clock[0]}

    def history(**kwargs):
        calls.append(kwargs)
        raise YFRateLimitError("throttled")

    module = _module(history)
    loader = lambda *args: reliability.yahoo_reference_history(module, *args)
    monkeypatch.setattr(router, "mark_provider_cooldown_live", mark)
    monkeypatch.setattr(router, "provider_cooldown_active_live", active)

    first = router.route_history("MSFT", "5d", "5m", loader)
    assert any(a.provider == "Yahoo Finance" and a.status == "rate_limited" for a in first.attempts)
    assert shared["Yahoo Finance"] == clock[0] + router.PROVIDER_RATE_LIMIT_COOLDOWN_SECONDS
    # Only the durable provider state is shared with another process.
    monkeypatch.setattr(router, "_provider_cooldowns", {})
    second = router.route_history("GOOG", "5d", "5m", loader)
    assert any(a.status == "provider_shared_cooldown" for a in second.attempts)
    assert len(calls) == 1

    clock[0] += router.PROVIDER_RATE_LIMIT_COOLDOWN_SECONDS + 1
    router.route_history("GOOGL", "5d", "5m", loader)
    assert len(calls) == 2


def test_missing_history_remains_an_empty_cooldown_result():
    def history(**kwargs):
        raise RuntimeError("missing Yahoo history")

    module = _module(history)
    assert reliability.yahoo_reference_history(module, "MSFT", "5d", "5m").empty
    assert reliability._cooldown_active(reliability._key("MSFT", "5d", "5m")) is True
