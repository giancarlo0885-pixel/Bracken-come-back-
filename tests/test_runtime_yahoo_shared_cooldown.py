"""Yahoo runtime failures must reach the router's cross-worker cooldown."""
from types import SimpleNamespace
from urllib.error import HTTPError as UrllibHTTPError

import pandas as pd
import pytest
import requests

import provider_router as router
import runtime_provider_reliability as reliability


class YFRateLimitError(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def reset_provider_runtime(monkeypatch):
    monkeypatch.setattr(reliability, "_UNAVAILABLE_UNTIL", {})
    monkeypatch.setattr(reliability, "_KEY_LOCKS", {})
    monkeypatch.setattr(router, "_provider_cooldowns", {})
    monkeypatch.setattr(router, "_symbol_cooldowns", {})
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


@pytest.mark.parametrize("status_code, expected", [(401, "authentication_failed"), (403, "access_denied"), (404, "http_not_found"), (503, "outage")])
@pytest.mark.parametrize("http_client", ["urllib", "requests"])
def test_runtime_http_failure_reaches_router_with_actual_status(monkeypatch, status_code, expected, http_client):
    calls = []
    if http_client == "urllib":
        error = UrllibHTTPError("https://query1.finance.yahoo.com/history", status_code, "unknown", None, None)
    else:
        error = requests.HTTPError("invalid symbol")
        error.response = SimpleNamespace(status_code=status_code)

    def history(**kwargs):
        calls.append(kwargs)
        raise error

    module = _module(history)
    loader = lambda *args: reliability.yahoo_reference_history(module, *args)
    monkeypatch.setattr(router, "provider_cooldown_active_live", lambda *args: {"active": False})
    monkeypatch.setattr(router, "mark_provider_cooldown_live", lambda *args, **kwargs: pytest.fail("non-rate Yahoo HTTP failure must remain request scoped"))

    first = router.route_history("MSFT", "1d", "1m", loader)
    attempt = next(a for a in first.attempts if a.provider == "Yahoo Finance")
    assert attempt.status == expected
    assert attempt.http_status == status_code
    assert first.metadata()["attempts"][-1]["http_status"] == status_code
    repeated = router.route_history("MSFT", "1d", "1m", loader)
    assert any(a.provider == "Yahoo Finance" and a.status == "request_cooldown" for a in repeated.attempts)
    assert len(calls) == 1
    alternative = router.route_history("MSFT", "5d", "5m", loader)
    assert any(a.provider == "Yahoo Finance" and a.status == expected and a.http_status == status_code for a in alternative.attempts)
    assert len(calls) == 2


@pytest.mark.parametrize("error, expected", [
    (requests.ConnectionError("connection failed"), "transport_error"),
    (requests.Timeout("timed out"), "timeout"),
    (requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0), "malformed_response"),
])
def test_runtime_transport_failure_reaches_router_without_becoming_no_data(monkeypatch, error, expected):
    def history(**kwargs):
        raise error

    module = _module(history)
    monkeypatch.setattr(router, "provider_cooldown_active_live", lambda *args: {"active": False})
    result = router.route_history("MSFT", "1d", "1m", lambda *args: reliability.yahoo_reference_history(module, *args))
    attempt = next(a for a in result.attempts if a.provider == "Yahoo Finance")
    assert attempt.status == expected
    assert attempt.http_status is None


def test_runtime_successful_empty_response_remains_no_data(monkeypatch):
    module = _module(lambda **kwargs: pd.DataFrame())
    monkeypatch.setattr(router, "provider_cooldown_active_live", lambda *args: {"active": False})
    result = router.route_history("MSFT", "1d", "1m", lambda *args: reliability.yahoo_reference_history(module, *args))
    attempt = next(a for a in result.attempts if a.provider == "Yahoo Finance")
    assert attempt.status == "no_data"
    assert attempt.http_status is None


@pytest.mark.parametrize("error", [YFRateLimitError("throttled"), UrllibHTTPError("https://query1.finance.yahoo.com/history", 429, "unknown", None, None)])
def test_runtime_router_same_request_recovers_after_existing_provider_and_key_cooldowns(monkeypatch, error):
    start = 1000.0
    clock = [start]
    shared = {}
    calls = []
    monkeypatch.setattr(router.time, "time", lambda: clock[0])
    monkeypatch.setattr(reliability.time, "monotonic", lambda: clock[0])

    def history(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise error
        return pd.DataFrame({"Close": [101.0]}, index=pd.DatetimeIndex(["2026-10-13T15:00:00Z"]))

    def mark(provider, seconds, reason):
        shared[provider] = clock[0] + seconds

    module = _module(history)
    loader = lambda *args: reliability.yahoo_reference_history(module, *args)
    monkeypatch.setattr(router, "mark_provider_cooldown_live", mark)
    monkeypatch.setattr(router, "provider_cooldown_active_live", lambda provider: {"active": shared.get(provider, 0) > clock[0]})
    first = router.route_history("MSFT", "5d", "5m", loader)
    assert any(a.provider == "Yahoo Finance" and a.status == "rate_limited" for a in first.attempts)
    provider_duration = router.PROVIDER_RATE_LIMIT_COOLDOWN_SECONDS
    key_duration = max(60, int(reliability.UNAVAILABLE_SYMBOL_COOLDOWN_SECONDS))
    assert shared["Yahoo Finance"] == start + provider_duration
    assert reliability._UNAVAILABLE_UNTIL[reliability._key("MSFT", "5d", "5m")] == start + key_duration

    clock[0] = start + provider_duration - 1
    assert router.route_history("MSFT", "5d", "5m", loader).frame.empty
    assert len(calls) == 1
    if key_duration > provider_duration:
        clock[0] = start + provider_duration + 1
        suppressed = router.route_history("MSFT", "5d", "5m", loader)
        assert suppressed.frame.empty
        assert any(a.provider == "Yahoo Finance" and a.status == "request_cooldown" for a in suppressed.attempts)
        assert len(calls) == 1
        assert not router._request_is_unavailable("Yahoo Finance", "MSFT", "5d", "5m")

    clock[0] = start + max(provider_duration, key_duration) + 1
    recovered = router.route_history("MSFT", "5d", "5m", loader)
    assert recovered.provider == "Yahoo Finance"
    assert recovered.frame.attrs["quote_verified"] is False
    assert len(calls) == 2
