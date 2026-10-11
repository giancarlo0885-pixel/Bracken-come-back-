"""Deep crypto forecasts require a verified current quote, not a daily-bar close."""
import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from forecasting import forecast_price
from market_sessions import latest_valid_bar_timestamp


def _daily_history(quote_time=None, *, quote_verified=True, index_end=None):
    index = pd.date_range(
        end=index_end or (datetime.now(timezone.utc) - timedelta(days=1)),
        periods=220,
        freq="D",
    )
    close = 100 + np.arange(220) * 0.02 + np.sin(np.arange(220)) * 0.2
    history = pd.DataFrame({"Close": close}, index=index)
    history.attrs["provider_route"] = {
        "requested_symbol": "BTC-USD",
        "provider_symbol": "BTC-USD",
        "interval": "1d",
        "quote_verified": quote_verified,
    }
    if quote_time is not None:
        history.attrs["provider_route"]["quote_timestamp"] = quote_time
    return history


@pytest.mark.parametrize("kind,verified,expected", [
    ("future", True, "future_live_quote_timestamp"),
    ("stale", True, "stale_live_quote_timestamp"),
    ("missing", True, "missing_live_quote_timestamp"),
    ("fresh", False, "unverified_live_quote"),
])
def test_deep_crypto_rejects_missing_or_invalid_live_quote(kind, verified, expected):
    now = datetime.now(timezone.utc)
    times = {
        "future": (now + timedelta(days=2)).replace(hour=23, minute=59).isoformat(),
        "stale": (now - timedelta(days=2)).isoformat(),
        "fresh": now.isoformat(),
        "missing": None,
    }
    history = _daily_history(times[kind], quote_verified=verified)
    previous_attrs = deepcopy(history.attrs)
    diagnostics = {}
    forecast = forecast_price(
        history, 5, market="crypto", source_interval="1d",
        diagnostics=diagnostics, require_live_quote=True,
    )
    assert forecast is None
    assert diagnostics["unavailable_reason"] == expected
    assert history.attrs == previous_attrs


def test_deep_crypto_rejects_future_daily_bar_close_even_if_marked_verified():
    future_index = datetime.now(timezone.utc) + timedelta(days=2)
    history = _daily_history(index_end=future_index)
    bar_close = latest_valid_bar_timestamp(history, "1d", symbol="BTC-USD")
    assert bar_close is not None
    assert bar_close.timestamp.hour == 23 and bar_close.timestamp.minute == 59
    history.attrs["provider_route"]["quote_timestamp"] = bar_close.timestamp.isoformat()

    diagnostics = {}
    assert forecast_price(
        history, 5, market="crypto", source_interval="1d",
        diagnostics=diagnostics, require_live_quote=True,
    ) is None
    assert diagnostics["unavailable_reason"] == "future_live_quote_timestamp"


def test_deep_crypto_accepts_verified_fresh_quote_without_changing_history():
    current_quote = datetime.now(timezone.utc).isoformat()
    history = _daily_history(current_quote)
    previous_attrs = deepcopy(history.attrs)
    previous_closes = history["Close"].copy(deep=True)

    forecast = forecast_price(
        history, 5, market="crypto", source_interval="1d",
        require_live_quote=True,
    )
    assert forecast is not None
    assert forecast.source_interval == "1d"
    assert forecast.source_quote_timestamp == current_quote
    assert history.attrs == previous_attrs
    pd.testing.assert_series_equal(history["Close"], previous_closes)


def test_historical_research_does_not_require_live_quote():
    history = _daily_history(quote_verified=False)
    assert forecast_price(
        history, 5, market="crypto", source_interval="1d",
        require_live_quote=False,
    ) is not None


def test_deep_worker_forecast_explicitly_requires_live_crypto_quote():
    source = Path(__file__).with_name("market_worker.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    scan = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "scan_market")
    forecast_calls = [
        node for node in ast.walk(scan)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "forecast_price"
    ]
    assert len(forecast_calls) == 1
    keyword = next((kw for kw in forecast_calls[0].keywords if kw.arg == "require_live_quote"), None)
    assert keyword is not None
    assert isinstance(keyword.value, ast.Compare)
    assert ast.unparse(keyword.value) == "market == 'crypto'"
