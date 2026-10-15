"""Request failures must preserve fallback breadth and provider provenance."""
from types import SimpleNamespace
from urllib.error import HTTPError as UrllibHTTPError

import pandas as pd
import pytest
import requests

import cache
import provider_capabilities
import provider_router as router
from alpaca_iex_provider import AlpacaDataError


@pytest.fixture(autouse=True)
def isolated_requests(monkeypatch):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    monkeypatch.setattr(cache, "_cache", {})
    monkeypatch.setattr(router, "_provider_cooldowns", {})
    monkeypatch.setattr(router, "_symbol_cooldowns", {})
    monkeypatch.setattr(router, "get_api_settings", lambda: {"POLYGON_API_KEY": "test-key"})
    monkeypatch.setattr(router, "capability_available", lambda *args: True)
    monkeypatch.setattr(router, "capability_priority_penalty", lambda *args: 0)
    monkeypatch.setattr(router, "record_capability_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(router, "provider_cooldown_active_live", lambda *args: {"active": False})
    monkeypatch.setattr(router, "reserve_provider_budget_live", lambda *args, **kwargs: {"reserved": True})
    monkeypatch.setattr(router, "mark_provider_cooldown_live", lambda *args, **kwargs: pytest.fail("request failure must not create a shared provider cooldown"))
    monkeypatch.setattr(router, "disable_capability", lambda *args, **kwargs: pytest.fail("request failure must not disable a provider capability"))
    monkeypatch.setattr(router, "_record_failure", lambda *args: None)


def _bars():
    return pd.DataFrame(
        {"Open": [100.0], "High": [102.0], "Low": [99.0], "Close": [101.0], "Volume": [200.0]},
        index=pd.DatetimeIndex(["2026-10-13T15:00:00Z"]),
    )


def _verified(provider, symbol, period, interval):
    return router._verified_history(_bars(), provider, symbol, symbol, period, interval, identity_verified=True)


def _http_error(status, detail):
    error = requests.HTTPError(detail)
    error.response = SimpleNamespace(status_code=status)
    return error


@pytest.mark.parametrize("alternative", [("5d", "5m"), ("5d", "1m"), ("1d", "5m")])
def test_empty_routes_do_not_block_other_periods_or_intervals(monkeypatch, alternative):
    calls = []
    yahoo_calls = []

    def polygon(symbol, period, interval, key):
        calls.append((symbol, period, interval))
        return pd.DataFrame() if (period, interval) == ("1d", "1m") else _verified("Polygon", symbol, period, interval)

    def yahoo(*args):
        yahoo_calls.append(args)
        return pd.DataFrame()

    monkeypatch.setattr(router, "_polygon", polygon)
    first = router.route_history("MSFT", "1d", "1m", yahoo)
    repeated = router.route_history("MSFT", "1d", "1m", yahoo)
    recovered = router.route_history("MSFT", *alternative, yahoo)

    assert first.frame.empty and repeated.frame.empty
    assert not router.symbol_is_unavailable("MSFT")
    assert calls == [("MSFT", "1d", "1m"), ("MSFT", *alternative)]
    assert yahoo_calls == [("MSFT", "1d", "1m")]
    assert any(a.provider == "Polygon" and a.status == "request_cooldown" for a in repeated.attempts)
    assert recovered.provider == recovered.frame.attrs["provider"] == "Polygon"
    assert recovered.frame.attrs["requested_symbol"] == recovered.frame.attrs["provider_symbol"] == "MSFT"
    assert recovered.frame.attrs["period"] == alternative[0]
    assert recovered.frame.attrs["interval"] == alternative[1]
    assert recovered.frame.attrs["quote_verified"] is True


@pytest.mark.parametrize("failure, expected_status", [
    (lambda: pd.DataFrame(), "no_data"),
    (lambda: _http_error(503, "invalid symbol; upgrade subscription"), "outage"),
    (lambda: _http_error(404, "symbol not found"), "http_not_found"),
    (lambda: _http_error(403, "forbidden"), "access_denied"),
    (lambda: requests.Timeout("transport timed out"), "timeout"),
])
def test_provider_failure_does_not_contaminate_other_provider_or_interval(monkeypatch, failure, expected_status):
    calls = []
    monkeypatch.setattr(router, "get_api_settings", lambda: {"POLYGON_API_KEY": "test-key", "FINNHUB_API_KEY": "test-key"})

    def polygon(symbol, period, interval, key):
        calls.append((symbol, period, interval))
        if interval == "1m":
            result = failure()
            if isinstance(result, Exception):
                raise result
            return result
        return _verified("Polygon", symbol, period, interval)

    monkeypatch.setattr(router, "_polygon", polygon)
    monkeypatch.setattr(router, "_finnhub", lambda symbol, period, interval, key: _verified("Finnhub", symbol, period, interval))
    first = router.route_history("MSFT", "1d", "1m", lambda *args: pytest.fail("verified fallback should succeed"))
    repeated = router.route_history("MSFT", "1d", "1m", lambda *args: pytest.fail("verified fallback should succeed"))
    alternate = router.route_history("MSFT", "5d", "5m", lambda *args: pytest.fail("Polygon should recover"))

    attempt = next(a for a in first.attempts if a.provider == "Polygon")
    assert attempt.status == expected_status
    assert attempt.http_status == (failure().response.status_code if expected_status in {"outage", "http_not_found", "access_denied"} else None)
    assert first.provider == repeated.provider == first.frame.attrs["provider"] == "Finnhub"
    assert any(a.provider == "Polygon" and a.status == "request_cooldown" for a in repeated.attempts)
    assert alternate.provider == alternate.frame.attrs["provider"] == "Polygon"
    assert alternate.frame.attrs["source_identity"] == "Polygon:MSFT:5d:5m"
    assert calls == [("MSFT", "1d", "1m"), ("MSFT", "5d", "5m")]


@pytest.mark.parametrize("http_status, detail, expected", [
    (401, "429 rate limit; premium subscription", "authentication_failed"),
    (429, "invalid symbol", "rate_limited"),
    (402, "invalid symbol", "payment_required"),
    (403, "forbidden", "access_denied"),
    (403, "premium entitlement required", "plan_limited"),
    (404, "unsupported invalid symbol", "http_not_found"),
    (500, "malformed JSON", "outage"),
    (503, "invalid symbol", "outage"),
    (503, "premium subscription; rate limit", "outage"),
    (422, "unsupported interval", "unsupported"),
    (400, "bad request", "http_error"),
])
def test_structured_http_status_takes_precedence(http_status, detail, expected):
    assert router.classify_provider_failure(_http_error(http_status, detail)) == expected


def test_typed_alpaca_error_preserves_status_and_timeout_classification():
    assert router.classify_provider_failure(AlpacaDataError("Alpaca market-data request failed", status_code=503)) == "outage"
    assert router.classify_provider_failure(AlpacaDataError("Alpaca market-data request failed", timed_out=True)) == "timeout"
    assert router.classify_provider_failure(AlpacaDataError("Alpaca market-data request failed")) == "transport_error"
    assert router.classify_provider_failure(requests.ConnectionError("connection failed")) == "transport_error"
    assert router.classify_provider_failure("Unsupported candle interval") == "unsupported"
    assert router.classify_provider_failure(requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0)) == "malformed_response"
    assert router.classify_provider_failure(AlpacaDataError("Alpaca returned malformed history response")) == "malformed_response"


def test_status_in_body_does_not_override_structured_status_or_invent_status():
    assert router._provider_status_from_exception(_http_error(503, "HTTP 401")) == 503
    assert router._provider_status_from_exception(RuntimeError("invalid symbol SYMBOL503")) is None
    assert router._provider_status_from_exception(RuntimeError("failed URL https://http429.example/data")) is None
    assert router._provider_status_from_exception(RuntimeError("symbol HTTP429 invalid")) is None
    assert router.classify_provider_failure("HTTP 503 invalid symbol") == "outage"
    assert router.classify_provider_failure("HTTP Error 503: invalid symbol") == "outage"


@pytest.mark.parametrize("http_status, expected", [(401, "authentication_failed"), (403, "access_denied"), (404, "http_not_found"), (429, "rate_limited"), (503, "outage")])
def test_urllib_http_error_preserves_typed_status(http_status, expected):
    error = UrllibHTTPError("https://example.com/history", http_status, "unknown", None, None)
    assert router._provider_status_from_exception(error) == http_status
    assert router.classify_provider_failure(error) == expected


@pytest.mark.parametrize("outcome", ["empty", "http", "yahoo_empty"])
def test_exact_request_recovers_after_existing_cooldown(monkeypatch, outcome):
    clock = [1000.0]
    calls = []
    monkeypatch.setattr(router.time, "time", lambda: clock[0])
    if outcome == "yahoo_empty":
        monkeypatch.setattr(router, "get_api_settings", lambda: {})

    def load(symbol, period, interval, *args):
        calls.append((symbol, period, interval))
        if len(calls) == 1:
            if outcome == "http":
                raise _http_error(503, "invalid symbol")
            return pd.DataFrame()
        return _bars() if outcome == "yahoo_empty" else _verified("Polygon", symbol, period, interval)

    monkeypatch.setattr(router, "_polygon", load)
    yahoo = load if outcome == "yahoo_empty" else lambda *args: pd.DataFrame()
    assert router.route_history("MSFT", "1d", "1m", yahoo).frame.empty
    assert router.route_history("MSFT", "1d", "1m", yahoo).frame.empty
    assert len(calls) == 1
    clock[0] += router.UNAVAILABLE_SYMBOL_COOLDOWN_SECONDS + 1
    recovered = router.route_history("MSFT", "1d", "1m", yahoo)
    assert recovered.provider == ("Yahoo Finance" if outcome == "yahoo_empty" else "Polygon")
    assert len(calls) == 2
    assert recovered.frame.attrs["quote_verified"] is (outcome != "yahoo_empty")


def test_explicit_confirmed_symbol_unavailability_still_blocks_all_requests(monkeypatch):
    monkeypatch.setattr(router, "_polygon", lambda *args: pytest.fail("confirmed unavailable symbol must be skipped"))
    router.mark_symbol_unavailable("MSFT")
    for period, interval in [("1d", "1m"), ("5d", "5m"), ("1y", "1d")]:
        result = router.route_history("MSFT", period, interval, lambda *args: pytest.fail("confirmed unavailable symbol must be skipped"))
        assert result.frame.empty
        assert result.attempts[0].status == "symbol_cooldown"


def test_alpha_basic_intraday_reserves_no_budget_and_daily_still_works(monkeypatch):
    budgets = []
    monkeypatch.setattr(router, "ALPHA_VANTAGE_PREMIUM", False)
    monkeypatch.setattr(router, "get_api_settings", lambda: {"ALPHA_VANTAGE_API_KEY": "test-key"})
    monkeypatch.setattr(router, "reserve_provider_budget_live", lambda *args, **kwargs: budgets.append((args, kwargs)) or {"reserved": True})
    monkeypatch.setattr(router, "_alpha", lambda symbol, period, interval, key: _verified("Alpha Vantage", symbol, period, interval) if interval == "1d" else pytest.fail("Basic intraday must not invoke its adapter"))

    intraday = router.route_history("MSFT", "1d", "1m", lambda *args: pd.DataFrame())
    assert budgets == []
    assert any(a.provider == "Alpha Vantage" and a.status == "capability_cooldown_or_unsupported" for a in intraday.attempts)
    daily = router.route_history("MSFT", "5d", "1d", lambda *args: pytest.fail("Alpha daily should succeed"))
    assert daily.provider == "Alpha Vantage"
    assert [args[:2] for args, _ in budgets] == [("Alpha Vantage", "__provider__"), ("Alpha Vantage", "us_history")]
    router.route_history("MSFT", "5d", "1d", lambda *args: pytest.fail("Alpha cache should succeed"))
    assert len(budgets) == 2


@pytest.mark.parametrize("interval, operation, other_operation", [("1m", "intraday_history", "daily_history"), ("1d", "daily_history", "intraday_history")])
def test_explicit_entitlement_disables_only_actual_history_operation(monkeypatch, interval, operation, other_operation):
    monkeypatch.setattr(provider_capabilities, "_cooldowns", {})
    monkeypatch.setattr(provider_capabilities, "_persist_capability", lambda *args: None)
    monkeypatch.setattr(provider_capabilities, "_load_persisted_cooldown", lambda *args: None)
    monkeypatch.setattr(router, "disable_capability", provider_capabilities.disable_capability)
    monkeypatch.setattr(router, "_polygon", lambda *args: (_ for _ in ()).throw(_http_error(403, "not entitled to this subscription endpoint")))

    result = router.route_history("MSFT", "5d", interval, lambda *args: pd.DataFrame())
    assert any(a.provider == "Polygon" and a.status == "capability_plan_limited" for a in result.attempts)
    assert provider_capabilities.capability_available("Polygon", operation) is False
    assert provider_capabilities.capability_available("Polygon", other_operation) is True
    assert provider_capabilities.capability_available("Polygon", "us_history") is True


@pytest.mark.parametrize("status_code, detail", [(403, "forbidden"), (503, "invalid symbol; upgrade subscription")])
def test_intraday_http_failure_does_not_demote_daily_route(monkeypatch, status_code, detail):
    monkeypatch.setattr(provider_capabilities, "_health", {})
    monkeypatch.setattr(provider_capabilities, "_cooldowns", {})
    monkeypatch.setattr(provider_capabilities, "_load_persisted_cooldown", lambda *args: None)
    monkeypatch.setattr(router, "record_capability_result", provider_capabilities.record_capability_result)
    monkeypatch.setattr(router, "capability_priority_penalty", provider_capabilities.capability_priority_penalty)
    monkeypatch.setattr(router, "capability_available", provider_capabilities.capability_available)
    monkeypatch.setattr(router, "get_api_settings", lambda: {"POLYGON_API_KEY": "test-key", "EODHD_API_KEY": "test-key"})

    def polygon(symbol, period, interval, key):
        if interval == "1m":
            raise _http_error(status_code, detail)
        return _verified("Polygon", symbol, period, interval)

    monkeypatch.setattr(router, "_polygon", polygon)
    monkeypatch.setattr(router, "_eodhd", lambda *args: pytest.fail("intraday failure must not demote the preferred daily route"))
    assert router.route_history("MSFT", "1d", "1m", lambda *args: pd.DataFrame()).frame.empty
    assert provider_capabilities.capability_health("Polygon", "intraday_history").status == "degraded"
    assert provider_capabilities.capability_health("Polygon", "daily_history").status == "healthy"
    assert provider_capabilities.capability_health("Polygon", "us_history").status == "healthy"
    daily = router.route_history("MSFT", "5d", "1d", lambda *args: pytest.fail("verified daily provider should succeed"))
    assert daily.provider == "Polygon"


@pytest.mark.parametrize("status_code, detail, limited", [
    (401, "subscription", False), (403, "forbidden", False), (404, "premium", False),
    (429, "subscription", False), (503, "upgrade subscription", False),
    (403, "not entitled under your current subscription", True),
])
def test_plan_classification_requires_entitlement_evidence(status_code, detail, limited):
    assert provider_capabilities.classify_plan_limited_status(status_code, detail) is limited


@pytest.mark.parametrize("provider, status_code, urllib_error", [("Alpaca IEX", 401, False), ("Polygon", 429, False), ("Yahoo Finance", 429, False), ("Yahoo Finance", 429, True)])
def test_shared_auth_and_rate_cooldowns_recover_same_request_on_existing_schedule(monkeypatch, provider, status_code, urllib_error):
    clock = [1000.0]
    shared = {}
    calls = []
    monkeypatch.setattr(router.time, "time", lambda: clock[0])
    monkeypatch.setattr(router, "get_api_settings", lambda: {"POLYGON_API_KEY": "test-key"} if provider == "Polygon" else {})
    if provider == "Alpaca IEX":
        monkeypatch.setenv("APCA_API_KEY_ID", "test-key")
        monkeypatch.setenv("APCA_API_SECRET_KEY", "test-secret")

    def load(symbol, period, interval, *args):
        calls.append((symbol, period, interval))
        if len(calls) == 1:
            if urllib_error:
                raise UrllibHTTPError("https://query1.finance.yahoo.com/history", status_code, "unknown", None, None)
            raise AlpacaDataError("Alpaca market-data HTTP 401", status_code=401) if provider == "Alpaca IEX" else _http_error(status_code, "rejected")
        return _bars() if provider in {"Alpaca IEX", "Yahoo Finance"} else _verified(provider, symbol, period, interval)

    def mark(name, seconds, reason):
        shared[name] = clock[0] + seconds

    monkeypatch.setattr(router, "mark_provider_cooldown_live", mark)
    monkeypatch.setattr(router, "provider_cooldown_active_live", lambda name: {"active": shared.get(name, 0) > clock[0]})
    monkeypatch.setattr(router, "iex_stock_history" if provider == "Alpaca IEX" else "_polygon", load)
    yahoo = load if provider == "Yahoo Finance" else lambda *args: pd.DataFrame()
    first = router.route_history("MSFT", "1d", "1m", yahoo)
    expected_status = "authentication_failed" if status_code == 401 else "rate_limited"
    assert any(a.provider == provider and a.status == expected_status and a.http_status == status_code for a in first.attempts)
    duration = router.PROVIDER_PERMISSION_COOLDOWN_SECONDS if status_code == 401 else router.PROVIDER_RATE_LIMIT_COOLDOWN_SECONDS
    assert shared[provider] == clock[0] + duration
    monkeypatch.setattr(router, "_provider_cooldowns", {})
    other_worker = router.route_history("GOOG", "5d", "5m", yahoo)
    assert any(a.provider == provider and a.status == "provider_shared_cooldown" for a in other_worker.attempts)
    assert len(calls) == 1
    clock[0] += duration + 1
    recovered = router.route_history("MSFT", "1d", "1m", yahoo)
    assert recovered.provider == provider
    assert len(calls) == 2
