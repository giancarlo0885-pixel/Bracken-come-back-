from __future__ import annotations

import math
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from entry_patterns import assess_dip_rebound, extract_entry_pattern, pattern_memory_features
from market_memory import _weighted_entry_summary, assess_market_memory, feature_vector
from technical_indicators import rsi


def rebound_history() -> pd.DataFrame:
    close = np.linspace(100, 103, 64)
    close[-8:] = [103, 103, 102.7, 102.1, 101.4, 100.8, 101.05, 101.3]
    frame = pd.DataFrame({"Open": close, "High": close + .4, "Low": close - .4,
                          "Close": close, "Volume": 1000.0},
                         index=pd.date_range("2026-09-12", periods=64, freq="5min", tz="UTC"))
    frame.loc[frame.index[-3:], "Low"] = [100.6, 100.9, 101.15]
    frame.loc[frame.index[-1], "Volume"] = 1500
    frame.attrs["interval"] = "5m"
    return frame


def observed_pattern() -> dict:
    frame = rebound_history()
    return extract_entry_pattern(frame, asof=(frame.index[-1] + pd.Timedelta(minutes=5)).to_pydatetime())


@pytest.mark.parametrize("prices,expected", [
    (list(range(100, 140)), 100), (list(range(140, 100, -1)), 0), ([100] * 40, 50),
    ([100, 99, 100, 98] + list(range(100, 140)), 100),
    ([100 + i % 2 for i in range(41)], 50),
])
def test_rsi_edges_and_latest_window(prices, expected):
    assert rsi(pd.Series(prices, dtype=float)) == pytest.approx(expected)


@pytest.mark.parametrize("prices", [[], [100] * 14, list(range(40)) + [np.nan], list(range(40)) + [np.inf]])
def test_rsi_missing_window_is_unknown(prices):
    assert math.isnan(rsi(pd.Series(prices, dtype=float)))


def test_confirmed_rebound_features_use_explicit_bar_horizon():
    pattern = observed_pattern()
    assert assess_dip_rebound(pattern)["qualified"]
    assert pattern["bars_since_trough"] == 2
    assert pattern["bar_minutes"] == 5
    assert pattern["rsi"] > pattern["trough_rsi"]
    assert pattern["momentum_5bars"] < 0
    assert pattern["higher_low"] is True


def test_partial_and_future_bars_cannot_change_the_pattern():
    frame = rebound_history()
    asof = (frame.index[-1] + pd.Timedelta(minutes=5)).to_pydatetime()
    expected = extract_entry_pattern(frame, asof=asof)
    extended = pd.concat([frame, pd.DataFrame({"Open": [1e9, 1e-6], "High": [1e9, 1e9],
                                             "Low": [1e-9, 1e-9], "Close": [1e9, 1e-9], "Volume": [1e9, 0]},
                                            index=pd.DatetimeIndex([asof, asof + timedelta(minutes=5)]))])
    extended.attrs = frame.attrs.copy()
    assert extract_entry_pattern(extended, asof=asof) == expected


@pytest.mark.parametrize("case", ["gap", "duplicate", "reversed", "naive", "missing_volume", "infinite", "invalid_range"])
def test_invalid_candles_cannot_supply_pattern_evidence(case):
    frame = rebound_history()
    if case == "gap":
        frame.index = frame.index.where(frame.index != frame.index[20], frame.index[20] + pd.Timedelta(minutes=1))
    elif case == "duplicate":
        frame.index = pd.DatetimeIndex(list(frame.index[:-1]) + [frame.index[-2]])
    elif case == "reversed":
        frame = frame.iloc[::-1]
    elif case == "naive":
        frame.index = frame.index.tz_localize(None)
    elif case == "missing_volume":
        frame = frame.drop(columns="Volume")
    elif case == "infinite":
        frame.loc[frame.index[-1], "Close"] = np.inf
    else:
        frame.loc[frame.index[-1], "High"] = 1
    assert extract_entry_pattern(frame)["available"] is False


@pytest.mark.parametrize("change", [
    {"higher_low": False}, {"rising_closes": False}, {"context_regime": -1},
    {"volume_ratio": .5}, {"rsi_delta": 0}, {"dip_depth_atr": 8}, {"bar_minutes": 60},
    {"rsi_delta": float("nan")}, {"bars_since_trough": 0},
])
def test_dip_alone_or_missing_confirmation_cannot_qualify(change):
    assert not assess_dip_rebound(dict(observed_pattern(), **change))["qualified"]


def test_memory_separates_timeframes_regimes_and_missing_legacy_features():
    signal = {"entry_pattern": observed_pattern(), "score": .6}
    current = dict(feature_vector(signal), pattern_bar_end_unix=signal["entry_pattern"]["bar_end_unix"]-3600)
    historical = [current, {k: v for k, v in current.items() if not k.startswith("pattern_")},
                  dict(current, pattern_bar_minutes=60), dict(current, pattern_context_regime=-1)]
    records = [{"return_pct": .02, "exit_time": "2026-09-12T05:00:00+00:00", "payload": {"features": features}} for features in historical]
    assert assess_market_memory(signal, records).analog_count == 1


def test_new_pattern_memory_rejects_future_or_unknown_outcomes():
    signal = {"entry_pattern": observed_pattern()}
    features = dict(feature_vector(signal), pattern_bar_end_unix=signal["entry_pattern"]["bar_end_unix"]-3600)
    rows = [{"return_pct": .5, "payload": {"features": features}, "exit_time": stamp}
            for stamp in (None, "2026-09-12T06:00:00+00:00", "invalid")]
    assert assess_market_memory(signal, rows).analog_count == 0


def test_memory_preserves_missing_features_and_mixed_lot_identity():
    pattern = observed_pattern()
    del pattern["zscore"]
    assert "pattern_zscore" not in pattern_memory_features({"entry_pattern": pattern})
    first = feature_vector({"entry_pattern": pattern})
    second = dict(first, pattern_bar_minutes=15)
    entries = [{"quantity_opened": 1, "features": f, "oracle_decision": {}} for f in (first, second)]
    assert not any(key.startswith("pattern_") for key in _weighted_entry_summary(entries)["features"])
