from types import SimpleNamespace

import pytest

import market_worker


def test_provider_diagnostic_uses_allowlisted_statuses(caplog):
    market_worker._log_fast_history_provider_diagnostics._last_logged = {}
    history = SimpleNamespace(attrs={"provider_route": {"attempts": [
        {"provider": "Polygon", "status": "provider_budget_blocked", "error": "SECRET_KEY"},
        {"provider": "Finnhub", "status": "capability_plan_limited", "error": "TOKEN"},
        {"provider": "UnknownProvider", "status": "secret_status", "error": "SENSITIVE"},
    ]}})
    with caplog.at_level("WARNING"):
        market_worker._log_fast_history_provider_diagnostics("NVDA", "5d", "5m", history)
        market_worker._log_fast_history_provider_diagnostics("NVDA", "5d", "5m", history)
    assert caplog.text.count("STOCK HISTORY PROVIDER DIAGNOSTIC") == 1
    assert "Polygon:provider_budget_blocked" in caplog.text
    assert "Finnhub:capability_plan_limited" in caplog.text
    assert "other:other" in caplog.text
    assert "SECRET_KEY" not in caplog.text
    assert "SENSITIVE" not in caplog.text


def test_provider_diagnostic_skips_missing_metadata(caplog):
    market_worker._log_fast_history_provider_diagnostics._last_logged = {}
    with caplog.at_level("WARNING"):
        market_worker._log_fast_history_provider_diagnostics("NVDA", "5d", "5m", None)
    assert "STOCK HISTORY PROVIDER DIAGNOSTIC" not in caplog.text


@pytest.mark.parametrize("status", ["authentication_failed", "access_denied", "http_not_found", "outage", "timeout", "transport_error", "unsupported", "no_data", "request_cooldown"])
def test_alpaca_provider_diagnostic_preserves_classification_without_error_details(caplog, status):
    market_worker._log_fast_history_provider_diagnostics._last_logged = {}
    history = SimpleNamespace(attrs={"provider_route": {"attempts": [
        {"provider": "Alpaca IEX", "status": status, "http_status": 503, "error": "SECRET_BODY https://example.com/?apikey=SECRET_TOKEN"},
    ]}})
    with caplog.at_level("WARNING"):
        market_worker._log_fast_history_provider_diagnostics("MSFT", "1d", "1m", history)
    assert f"Alpaca IEX:{status}" in caplog.text
    assert "http=503" in caplog.text
    assert "SECRET" not in caplog.text
    assert "https://" not in caplog.text


@pytest.mark.parametrize("http_status", [None, True, False, "503", "SECRET_TOKEN", 503.0, 99, 600])
def test_provider_diagnostic_omits_untyped_or_invalid_http_status(caplog, http_status):
    market_worker._log_fast_history_provider_diagnostics._last_logged = {}
    history = SimpleNamespace(attrs={"provider_route": {"attempts": [
        {"provider": "Alpaca IEX", "status": "outage", "http_status": http_status},
    ]}})
    with caplog.at_level("WARNING"):
        market_worker._log_fast_history_provider_diagnostics("MSFT", "1d", "1m", history)
    assert "Alpaca IEX:outage" in caplog.text
    assert "http=" not in caplog.text
    assert "SECRET" not in caplog.text
