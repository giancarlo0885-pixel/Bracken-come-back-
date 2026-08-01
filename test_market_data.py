from __future__ import annotations

import pandas as pd

from market_data import _snapshot_from_history


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
