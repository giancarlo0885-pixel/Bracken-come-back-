"""Successful Yahoo cash fallbacks must retain safe primary-provider visibility."""
from copy import deepcopy
from datetime import datetime, timezone
import time

import pandas as pd
import pytest

import market_data as data
import market_worker
from provider_router import ProviderAttempt, RoutedHistory


@pytest.fixture(autouse=True)
def isolated_diagnostics(monkeypatch):
    monkeypatch.setattr(data._log_stock_history_provider_diagnostics, "_last_logged", {}, raising=False)
    monkeypatch.setattr(data, "EXECUTION_MODE", "paper")
    monkeypatch.setattr(data, "PAPER_BROKER_MODE", True)
    monkeypatch.setattr(data, "quote_is_fresh", lambda *args, **kwargs: True)


def _route(provider="Yahoo Finance", symbol="MSFT", attempts=None):
    now = datetime.now(timezone.utc)
    frame = pd.DataFrame({"Close": [100.0], "Volume": [200.0]}, index=pd.DatetimeIndex([now]))
    frame.attrs.update(
        provider=provider, requested_symbol=symbol, provider_symbol=symbol,
        provider_native_symbol=symbol, quote_verified=provider != "Yahoo Finance",
        source_identity=f"{provider}:{symbol}:1d:1m", cache_identity="cached-history",
        source_mode="strict_research_fallback" if provider == "Yahoo Finance" else "provider",
    )
    if attempts is None:
        attempts = [
            ProviderAttempt("Alpaca IEX", False, status="provider_budget_blocked", error="SECRET_KEY"),
            ProviderAttempt("Polygon", False, status="access_denied", error="https://example.com/?apikey=SECRET_TOKEN", http_status=403),
            ProviderAttempt(provider, True, 1, "strict_research_fallback" if provider == "Yahoo Finance" else "healthy"),
        ]
    return RoutedHistory(frame, provider, attempts, now.isoformat())


@pytest.mark.parametrize("interval, paper_reference", [("1m", True), ("1d", False)])
def test_common_history_path_logs_successful_yahoo_research_and_paper_fallback(monkeypatch, caplog, interval, paper_reference):
    routed = _route()
    original_attrs = deepcopy(routed.frame.attrs)
    original_attempts = deepcopy(routed.metadata()["attempts"])
    monkeypatch.setattr(data, "route_history", lambda *args: routed)
    monkeypatch.setattr(data, "latest_bar_timestamp", lambda *args, **kwargs: datetime.now(timezone.utc))

    with caplog.at_level("WARNING"):
        history = data.get_history("MSFT", "1d", interval)

    assert "selected_provider=Yahoo Finance" in caplog.text
    assert "period=1d" in caplog.text and f"interval={interval}" in caplog.text
    assert "Alpaca IEX:provider_budget_blocked" in caplog.text
    assert "Polygon:access_denied(http=403)" in caplog.text
    assert "Yahoo Finance:strict_research_fallback" in caplog.text
    assert "SECRET" not in caplog.text and "https://" not in caplog.text
    assert history.attrs["provider_route"]["attempts"] == original_attempts
    assert history.attrs["provider"] == history.attrs["provider_route"]["provider"] == "Yahoo Finance"
    assert history.attrs["provider_quote_verified"] is False
    assert history.attrs["paper_reference_verified"] is paper_reference
    assert history.attrs["provider_route"]["paper_reference_verified"] is paper_reference
    assert history.attrs["cache_identity"] == "cached-history"
    assert routed.frame.attrs == original_attrs


@pytest.mark.parametrize("provider, symbol, attempts", [
    ("Alpaca IEX", "MSFT", [ProviderAttempt("Alpaca IEX", True, 1, "healthy")]),
    ("Polygon", "MSFT", [ProviderAttempt("Alpaca IEX", False, status="not_configured"), ProviderAttempt("Polygon", True, 1, "healthy")]),
    ("Yahoo Finance", "BTC-USD", None),
    ("Yahoo Finance", "MSFT", [ProviderAttempt("Yahoo Finance", True, 1, "strict_research_fallback")]),
    ("Yahoo Finance", "MSFT", []),
])
def test_common_history_path_does_not_log_primary_crypto_or_unexplained_success(monkeypatch, caplog, provider, symbol, attempts):
    monkeypatch.setattr(data, "route_history", lambda *args: _route(provider, symbol, attempts))
    with caplog.at_level("WARNING"):
        history = data.get_history(symbol, "1d", "1m")
    assert not history.empty
    assert "STOCK HISTORY PROVIDER DIAGNOSTIC" not in caplog.text


def test_cached_history_repeats_and_worker_alias_share_existing_throttle(monkeypatch, caplog):
    clock = [1000.0]
    routed = _route()
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(data, "route_history", lambda *args: routed)
    assert market_worker._log_fast_history_provider_diagnostics is data._log_stock_history_provider_diagnostics

    with caplog.at_level("WARNING"):
        history = data.get_history("MSFT", "1d", "1m")
        data.get_history("MSFT", "1d", "1m")
        market_worker._log_fast_history_provider_diagnostics("MSFT", "1d", "1m", history)
        clock[0] += 299
        data.get_history("MSFT", "1d", "1m")
        assert caplog.text.count("STOCK HISTORY PROVIDER DIAGNOSTIC") == 1
        clock[0] += 1
        data.get_history("MSFT", "1d", "1m")
    assert caplog.text.count("STOCK HISTORY PROVIDER DIAGNOSTIC") == 2


@pytest.mark.parametrize("status, http_status, expected", [
    ("authentication_failed", 401, "Alpaca IEX:authentication_failed(http=401)"),
    ("capability_plan_limited", 403, "Alpaca IEX:capability_plan_limited(http=403)"),
    ("provider_budget_blocked", "SECRET_HTTP", "Alpaca IEX:provider_budget_blocked"),
    ("SECRET_STATUS", True, "Alpaca IEX:other"),
    ("outage", 503.0, "Alpaca IEX:outage"),
    ("outage", 600, "Alpaca IEX:outage"),
])
def test_successful_fallback_logs_safe_actual_attempt_outcomes(monkeypatch, caplog, status, http_status, expected):
    attempts = [
        ProviderAttempt("Alpaca IEX", False, status=status, error="SECRET_BODY https://example.com/?apikey=SECRET_TOKEN", http_status=http_status),
        ProviderAttempt("Yahoo Finance", True, 1, "strict_research_fallback"),
    ]
    monkeypatch.setattr(data, "route_history", lambda *args: _route(attempts=attempts))
    with caplog.at_level("WARNING"):
        data.get_history("MSFT", "1d", "1m")
    assert expected in caplog.text
    assert "SECRET" not in caplog.text and "https://" not in caplog.text
    if type(http_status) is not int or not 100 <= http_status <= 599:
        assert "http=" not in caplog.text


@pytest.mark.parametrize("failure", ["logger", "malformed_attempts"])
def test_diagnostic_failure_cannot_make_successful_history_unavailable(monkeypatch, caplog, failure):
    routed = _route()
    if failure == "logger":
        monkeypatch.setattr(data, "_log_stock_history_provider_diagnostics", lambda *args: (_ for _ in ()).throw(RuntimeError("SECRET_EXCEPTION")))
    else:
        metadata = routed.metadata()
        metadata["attempts"] = 42
        monkeypatch.setattr(routed, "metadata", lambda: metadata)
    monkeypatch.setattr(data, "route_history", lambda *args: routed)
    with caplog.at_level("DEBUG"):
        history = data.get_history("MSFT", "1d", "1m")
    assert list(history["Close"]) == [100.0]
    assert history.attrs["provider"] == "Yahoo Finance"
    assert history.attrs["provider_quote_verified"] is False
    assert "SECRET_EXCEPTION" not in caplog.text


def test_shared_diagnostic_limit_preserves_active_throttle_and_admits_after_expiry(monkeypatch):
    clock = [1000.0]
    calls = []
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(data.log, "warning", lambda *args: calls.append(args))
    frame = _route().frame
    frame.attrs["provider_route"] = _route().metadata()
    for index in range(513):
        data._log_stock_history_provider_diagnostics(f"SYM{index}", "1d", "1m", frame)
        clock[0] += 0.1
    logged = data._log_stock_history_provider_diagnostics._last_logged
    assert len(logged) == 512
    assert any(key[0] == "SYM0" for key in logged)
    assert all(key[0] != "SYM512" for key in logged)
    assert len(calls) == 512
    data._log_stock_history_provider_diagnostics("SYM0", "1d", "1m", frame)
    data._log_stock_history_provider_diagnostics("SYM512", "1d", "1m", frame)
    assert len(calls) == 512
    clock[0] = 1300.0
    data._log_stock_history_provider_diagnostics("SYM512", "1d", "1m", frame)
    assert len(logged) == 512
    assert all(key[0] != "SYM0" for key in logged)
    assert any(key[0] == "SYM512" for key in logged)
    assert len(calls) == 513


def test_shared_diagnostic_uses_bounded_safe_request_fields(caplog):
    frame = _route().frame
    frame.attrs["provider_route"] = _route().metadata()
    with caplog.at_level("WARNING"):
        data._log_stock_history_provider_diagnostics("MSFT\nSECRET_INJECTED", "SECRET_PERIOD", "SECRET_INTERVAL", frame)
    assert "symbol=other | period=other | interval=other" in caplog.text
    assert "SECRET" not in caplog.text
