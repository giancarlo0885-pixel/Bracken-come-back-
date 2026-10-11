"""Fast crypto forecasts must not use stale or unverified provider quotes."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from forecasting import forecast_price


def _history(timestamp):
    idx = pd.date_range(end=datetime.now(timezone.utc), periods=220, freq="min")
    prices = 100.0 + np.arange(220) * 0.02 + np.sin(np.arange(220)) * 0.2
    frame = pd.DataFrame({"Close": prices}, index=idx)
    frame.attrs["provider_route"] = {
        "requested_symbol": "BTC-USD",
        "provider_symbol": "BTC-USD",
        "interval": "1m",
    }
    if timestamp is not None:
        frame.attrs["provider_route"]["quote_timestamp"] = timestamp
    return frame


@pytest.mark.parametrize("timestamp,reason", [
    (None, "missing_live_quote_timestamp"),
    ("not-a-time", "invalid_live_quote_timestamp"),
    ("2026-01-01T00:00:00", "unverified_live_quote_timezone"),
    ("2020-01-01T00:00:00Z", "stale_live_quote_timestamp"),
    ("2099-01-01T00:00:00Z", "future_live_quote_timestamp"),
])
def test_fast_crypto_rejects_unverified_quote(timestamp, reason):
    diagnostics = {}
    with patch("forecasting.predict_crypto_direction") as predictor:
        result = forecast_price(
            _history(timestamp), source_interval="1m", horizon_bars=5,
            asset_class="crypto", diagnostics=diagnostics, require_live_quote=True,
        )
    assert result is None
    assert diagnostics["unavailable_reason"] == reason
    predictor.assert_not_called()


def test_fast_crypto_passes_fresh_quote_to_selective_model():
    diagnostics = {}
    fresh = datetime.now(timezone.utc).isoformat()
    with patch("forecasting.predict_crypto_direction", return_value=None) as predictor:
        result = forecast_price(
            _history(fresh), source_interval="1m", horizon_bars=5,
            asset_class="crypto", diagnostics=diagnostics, require_live_quote=True,
        )
    assert result is None
    assert diagnostics["unavailable_reason"] == "causal_model_abstention"
    predictor.assert_called_once()
