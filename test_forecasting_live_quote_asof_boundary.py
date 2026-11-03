"""Live paper forecasts must not consume bars unavailable at their quote timestamp.

Synthetic point-in-time tests only; no execution, strategy or accounting changes.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from forecasting import forecast_price


def _history(*, quote_age_seconds=1, index_kind="aware"):
    now = datetime.now(timezone.utc)
    index = pd.date_range(end=now - timedelta(seconds=10), periods=220, freq="min")
    if index_kind == "naive":
        index = index.tz_localize(None)
    elif index_kind == "integer":
        index = pd.RangeIndex(220)
    elif index_kind == "missing":
        values = list(index)
        values[50] = pd.NaT
        index = pd.DatetimeIndex(values)
    prices = 100 + np.linspace(0, 2, 220) + 0.05 * np.sin(np.arange(220))
    history = pd.DataFrame({"Close": prices}, index=index)
    history.attrs["provider_route"] = {
        "requested_symbol": "BTC-USD",
        "provider_symbol": "BTC-USD",
        "interval": "1m",
        "quote_verified": True,
        "quote_timestamp": (now - timedelta(seconds=quote_age_seconds)).isoformat(),
    }
    return history


def _attempt(history, *, require_live_quote=True):
    diagnostics = {}
    with patch("forecasting.predict_crypto_direction", return_value={
        "probability_up": 0.55,
        "predicted_log_return": 0.0002,
        "vol_per_bar": 0.001,
    }) as predictor:
        result = forecast_price(
            history, days=None, source_interval="1m", horizon_bars=5,
            market="crypto", asset_class="crypto",
            require_live_quote=require_live_quote, diagnostics=diagnostics,
        )
    return result, diagnostics, predictor


def test_bar_after_quote_cannot_inform_quote_anchored_paper_forecast():
    # The bar is historical relative to the current clock but 30 seconds
    # newer than the price quote it would purport to explain.
    result, diagnostics, predictor = _attempt(_history(quote_age_seconds=40))
    assert result is None
    assert diagnostics["unavailable_reason"] == "history_bar_after_live_quote"
    predictor.assert_not_called()


@pytest.mark.parametrize("index_kind", ["naive", "integer", "missing"])
def test_unverifiable_history_chronology_cannot_pass_live_quote_gate(index_kind):
    result, diagnostics, predictor = _attempt(_history(index_kind=index_kind))
    assert result is None
    assert diagnostics["unavailable_reason"] == "unverifiable_live_history_timestamps"
    predictor.assert_not_called()


def test_verified_earlier_history_reaches_existing_selective_model():
    result, diagnostics, predictor = _attempt(_history(quote_age_seconds=1))
    assert result is not None
    assert "unavailable_reason" not in diagnostics
    predictor.assert_called_once()


def test_historical_research_keeps_non_live_index_policy():
    result, diagnostics, predictor = _attempt(
        _history(index_kind="naive"), require_live_quote=False,
    )
    assert result is not None
    assert "unavailable_reason" not in diagnostics
    predictor.assert_called_once()
