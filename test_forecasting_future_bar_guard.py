"""A fresh execution quote cannot certify OHLC observations from the future."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import numpy as np
import pandas as pd

from forecasting import forecast_price


def _history(*, future_position=None):
    now = datetime.now(timezone.utc)
    index = pd.date_range(end=now - timedelta(seconds=2), periods=220, freq="min")
    if future_position is not None:
        values = list(index)
        values[future_position] = pd.Timestamp(now + timedelta(minutes=15))
        index = pd.DatetimeIndex(values)
    close = 100 + np.linspace(0, 3, len(index)) + 0.05 * np.sin(np.arange(len(index)))
    frame = pd.DataFrame({"Close": close}, index=index)
    frame.attrs["provider_route"] = {
        "requested_symbol": "BTC-USD", "provider_symbol": "BTC-USD",
        "interval": "1m", "quote_verified": True,
        "quote_timestamp": now.isoformat(),
    }
    return frame


def _forecast(history, *, require_live_quote=True):
    diagnostics = {}
    with patch("forecasting.predict_crypto_direction", return_value={
        "probability_up": 0.55, "predicted_log_return": 0.0002,
        "vol_per_bar": 0.001,
    }) as predictor:
        result = forecast_price(
            history, days=None, source_interval="1m", horizon_bars=5,
            market="crypto", asset_class="crypto", diagnostics=diagnostics,
            require_live_quote=require_live_quote,
        )
    return result, diagnostics, predictor


def test_future_final_bar_is_not_certified_by_a_fresh_quote():
    result, diagnostics, predictor = _forecast(_history(future_position=-1))
    assert result is None
    assert diagnostics["unavailable_reason"] == "future_history_bar_timestamp"
    predictor.assert_not_called()


def test_future_interior_bar_is_not_hidden_by_a_current_final_bar():
    result, diagnostics, predictor = _forecast(_history(future_position=40))
    assert result is None
    assert diagnostics["unavailable_reason"] == "future_history_bar_timestamp"
    predictor.assert_not_called()


def test_current_bars_still_reach_the_existing_selective_model():
    result, diagnostics, predictor = _forecast(_history())
    assert result is not None
    assert result.model == "crypto selective sign transition"
    assert "unavailable_reason" not in diagnostics
    predictor.assert_called_once()


def test_non_live_historical_research_keeps_its_original_boundary():
    result, diagnostics, predictor = _forecast(
        _history(future_position=-1), require_live_quote=False,
    )
    assert result is not None
    assert "unavailable_reason" not in diagnostics
    predictor.assert_called_once()
