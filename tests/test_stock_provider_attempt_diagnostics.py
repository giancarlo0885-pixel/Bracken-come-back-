from types import SimpleNamespace

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
