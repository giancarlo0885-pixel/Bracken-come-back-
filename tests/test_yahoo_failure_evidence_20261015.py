"""Regression checks for Yahoo failure classification without changing quote eligibility."""
from unittest.mock import Mock

import pandas as pd
import pytest

import runtime_provider_reliability as reliability


@pytest.mark.parametrize(
    ("status", "expected_class"),
    [(429, "rate_limited"), (401, "authentication_failed"), (503, "outage")],
)
def test_yahoo_http_failure_is_classified_and_never_marked_as_unsupported(
    monkeypatch, caplog, status, expected_class
):
    from provider_router import classify_provider_failure

    error = RuntimeError("Yahoo request failed")
    error.response = Mock(status_code=status)
    assert classify_provider_failure(error) == expected_class

    ticker = Mock()
    ticker.history.side_effect = error
    market_data = Mock()
    market_data.yf.Ticker.return_value = ticker
    monkeypatch.setattr(reliability, "_cooldown_active", lambda key: False)
    monkeypatch.setattr(reliability, "_mark_unavailable", lambda key: None)
    with caplog.at_level("INFO", logger="runtime-provider-reliability"):
        if status == 429:
            with pytest.raises(RuntimeError):
                reliability.yahoo_reference_history(market_data, "AAPL", "1d", "1m")
        else:
            assert reliability.yahoo_reference_history(market_data, "AAPL", "1d", "1m").empty

    assert "coverage=UNVERIFIED" in caplog.text
    assert f"http_status={status}" in caplog.text
    assert f"failure_class={expected_class}" in caplog.text
    assert "Yahoo request failed" not in caplog.text


def test_yahoo_history_missing_is_not_executable_quote(monkeypatch):
    monkeypatch.setattr(reliability, "_cooldown_active", lambda key: False)
    monkeypatch.setattr(reliability, "_mark_unavailable", lambda key: None)
    market_data = Mock()
    market_data.yf.Ticker.return_value.history.return_value = pd.DataFrame()
    market_data._normalize.return_value = pd.DataFrame()
    assert reliability.yahoo_reference_history(market_data, "MSFT", "1d", "1m").empty
