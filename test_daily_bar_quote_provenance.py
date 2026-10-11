"""Daily OHLCV close boundaries must not masquerade as provider quote times."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import numpy as np
import pandas as pd

from market_data import get_history
from provider_router import RoutedHistory


def _routed_daily_history(*, provider_quote_timestamp=None):
    future_day = datetime.now(timezone.utc) + timedelta(days=1)
    index = pd.date_range(end=future_day, periods=45, freq="D")
    frame = pd.DataFrame({
        "Open": 100 + np.arange(45),
        "High": 101 + np.arange(45),
        "Low": 99 + np.arange(45),
        "Close": 100 + np.arange(45),
        "Volume": np.ones(45),
    }, index=index)
    frame.attrs.update({
        "requested_symbol": "BTC-USD",
        "provider_symbol": "BTC-USD",
        "interval": "1d",
        "quote_verified": False,
    })
    if provider_quote_timestamp is not None:
        frame.attrs["quote_timestamp"] = provider_quote_timestamp
    return RoutedHistory(frame, "Test historical provider", [], datetime.now(timezone.utc).isoformat())


def test_daily_bar_close_is_not_a_live_quote_timestamp():
    routed = _routed_daily_history()
    with patch("market_data.route_history", return_value=routed):
        history = get_history("BTC-USD", "1y", "1d")
    route = history.attrs["provider_route"]
    assert route["latest_bar_timestamp"].endswith("23:59:00+00:00")
    assert route.get("quote_timestamp") is None
    assert route["quote_verified"] is False
    assert route["interval"] == "1d"
    assert len(history) == 45


def test_genuine_provider_quote_timestamp_is_not_overwritten_by_bar_close():
    provider_time = datetime.now(timezone.utc).isoformat()
    routed = _routed_daily_history(provider_quote_timestamp=provider_time)
    with patch("market_data.route_history", return_value=routed):
        history = get_history("BTC-USD", "1y", "1d")
    route = history.attrs["provider_route"]
    assert route["quote_timestamp"] == provider_time
    assert route["latest_bar_timestamp"] != provider_time
