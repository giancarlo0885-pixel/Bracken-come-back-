from __future__ import annotations

import pandas as pd
import numpy as np

from market_data import _snapshot_from_history, finite_scalar


def _history(close, volume=None):
    index = pd.date_range("2026-01-01", periods=len(close), tz="UTC")
    data = {"Close": close}
    if volume is not None:
        data["Volume"] = volume
    return pd.DataFrame(data, index=index)


def test_snapshot_rejects_all_nan_close():
    assert _snapshot_from_history("BTC-USD", _history([np.nan, np.nan], [1, 2]), "1d") is None


def test_finite_scalar_empty_series_returns_none():
    assert finite_scalar(pd.Series([], dtype=float)) is None


def test_snapshot_handles_all_nan_volume_as_zero():
    snapshot = _snapshot_from_history("BTC-USD", _history([100, 101], [np.nan, np.nan]), "1d")
    assert snapshot is not None
    assert snapshot.volume == 0.0


def test_snapshot_rejects_infinite_close():
    assert _snapshot_from_history("BTC-USD", _history([float("inf")], [2]), "1d") is None


def test_snapshot_handles_infinite_volume_as_zero():
    snapshot = _snapshot_from_history("BTC-USD", _history([100, 101], [1, float("inf")]), "1d")
    assert snapshot is not None
    assert snapshot.volume == 1.0


def test_snapshot_accepts_one_row_dataframe():
    snapshot = _snapshot_from_history("AAPL", _history([200], [1000]), "1d")
    assert snapshot is not None
    assert snapshot.price == 200.0
    assert snapshot.change_pct == 0.0


def test_snapshot_handles_dataframe_shaped_close_and_volume_columns():
    index = pd.date_range("2026-01-01", periods=3, tz="UTC")
    history = pd.DataFrame(
        {
            ("Close", "BTC-USD"): [100.0, 105.0, 110.0],
            ("Volume", "BTC-USD"): [1000.0, 1200.0, 1500.0],
        },
        index=index,
    )
    history.columns = pd.MultiIndex.from_tuples(history.columns)
    snapshot = _snapshot_from_history("BTC-USD", history, "1d")
    assert snapshot is not None
    assert snapshot.price == 110.0
    assert snapshot.volume == 1500.0


def test_snapshot_uses_latest_bar_timestamp_and_preserves_fetched_at():
    history = _history([100, 101], [1000, 1100])
    history.attrs["provider_route"] = {
        "provider": "unit",
        "fetched_at": "2026-08-01T12:00:00+00:00",
    }
    snapshot = _snapshot_from_history("AAPL", history, "1d")
    assert snapshot is not None
    assert snapshot.timestamp == "2026-01-02T00:00:00+00:00"
    assert snapshot.fetched_at == "2026-08-01T12:00:00+00:00"
