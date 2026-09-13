from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from entry_pattern_memory_runtime import expanded_feature_vector
from schwager_technical_framework import assess_schwager_technical_structure


def _frame(closes: list[float]) -> pd.DataFrame:
    close = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close,
            "Low": close,
            "Close": close,
            "Volume": np.full(len(close), 1000.0),
        }
    )


def test_detects_upside_range_breakout_without_forcing_trade() -> None:
    base = list(np.linspace(100.0, 110.0, 79))
    history = _frame(base + [116.0])
    result = assess_schwager_technical_structure(history)

    assert result.available is True
    assert result.trend_state == "uptrend"
    assert result.breakout_state == "upside_breakout"
    assert result.breakout_score > 0
    assert result.pattern_tag == "range_breakout_up"
    assert result.support is not None
    assert result.resistance is not None
    assert result.suggested_stop is not None
    assert result.objective_1 is not None
    assert result.reason.startswith("Schwager TA:")


def test_failed_upside_breakout_is_recorded_as_bearish_evidence() -> None:
    closes = list(np.linspace(100.0, 104.0, 78)) + [112.0, 103.0]
    result = assess_schwager_technical_structure(_frame(closes))

    assert result.available is True
    assert result.failed_breakout is True
    assert result.breakout_state == "failed_upside_breakout"
    assert result.breakout_score < 0
    assert result.pattern_tag == "failed_breakout_bearish"


def test_schwager_features_enter_market_memory_vector() -> None:
    signal = SimpleNamespace(
        price=100.0,
        rsi_14=45.0,
        schwager_trend_score=0.6,
        schwager_breakout_score=0.8,
        schwager_oscillator_score=0.3,
        schwager_setup_score=0.65,
        schwager_failed_breakout=True,
        schwager_support_distance_pct=0.02,
        schwager_resistance_distance_pct=0.04,
    )
    features = expanded_feature_vector(signal)

    assert features["schwager_trend_score"] == 0.6
    assert features["schwager_breakout_score"] == 0.8
    assert features["schwager_setup_score"] == 0.65
    assert features["schwager_failed_breakout"] == 1.0
    assert features["schwager_support_distance"] == 0.1
    assert features["schwager_resistance_distance"] == 0.2


def test_short_history_fails_closed() -> None:
    result = assess_schwager_technical_structure(_frame([100.0, 101.0, 102.0]))
    assert result.available is False
    assert result.setup_score == 0.0
