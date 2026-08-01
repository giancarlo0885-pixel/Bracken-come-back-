from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import numpy as np

from cache import cached_call
from market_data import (
    MarketSnapshot,
    _duplicate_price_quarantine,
    _normalize,
    _snapshot_from_history,
    finite_scalar,
    get_many_snapshots,
)
from provider_router import _redact_url, _verified_history


def _history(close, volume=None, symbol="BTC-USD"):
    index = pd.date_range("2026-01-01", periods=len(close), tz="UTC")
    data = {"Close": close}
    if volume is not None:
        data["Volume"] = volume
    frame = pd.DataFrame(data, index=index)
    frame.attrs["requested_symbol"] = symbol.upper()
    frame.attrs["provider_symbol"] = symbol.upper()
    frame.attrs["provider_route"] = {
        "provider": "unit",
        "requested_symbol": symbol.upper(),
        "provider_symbol": symbol.upper(),
        "interval": "1d",
    }
    return frame


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
    snapshot = _snapshot_from_history("AAPL", _history([200], [1000], "AAPL"), "1d")
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
    history.attrs["requested_symbol"] = "BTC-USD"
    history.attrs["provider_symbol"] = "BTC-USD"
    history.attrs["provider_route"] = {
        "provider": "unit",
        "requested_symbol": "BTC-USD",
        "provider_symbol": "BTC-USD",
        "interval": "1d",
    }
    snapshot = _snapshot_from_history("BTC-USD", history, "1d")
    assert snapshot is not None
    assert snapshot.price == 110.0
    assert snapshot.volume == 1500.0


def test_snapshot_uses_latest_bar_timestamp_and_preserves_fetched_at():
    history = _history([100, 101], [1000, 1100], "AAPL")
    history.attrs["provider_route"] = {
        "provider": "unit",
        "fetched_at": "2026-08-01T12:00:00+00:00",
    }
    snapshot = _snapshot_from_history("AAPL", history, "1d")
    assert snapshot is not None
    assert snapshot.timestamp == "2026-01-02T23:59:00+00:00"
    assert snapshot.fetched_at == "2026-08-01T12:00:00+00:00"


def test_gm_cannot_receive_driv_history():
    history = _history([88.59], [1000], "DRIV")
    assert _snapshot_from_history("GM", history, "1d") is None


def test_ford_cannot_receive_another_ticker_price():
    history = _history([162.00], [1000], "AAPL")
    assert _snapshot_from_history("F", history, "1d") is None


def test_foreign_symbols_cannot_share_another_symbol_dataframe():
    history = _history([3383.50], [1000], "7203.T")
    assert _snapshot_from_history("SAP.DE", history, "1d") is None


def test_multiindex_data_selects_exact_requested_ticker():
    index = pd.date_range("2026-01-01", periods=2, tz="UTC")
    frame = pd.DataFrame(
        {
            ("Close", "DRIV"): [88.59, 88.59],
            ("Close", "GM"): [40.0, 41.0],
            ("Volume", "DRIV"): [10, 10],
            ("Volume", "GM"): [20, 21],
        },
        index=index,
    )
    frame.columns = pd.MultiIndex.from_tuples(frame.columns)
    selected = _normalize(frame, "GM")
    assert list(selected["Close"]) == [40.0, 41.0]
    assert list(selected["Volume"]) == [20, 21]


def test_missing_ticker_data_returns_no_result():
    index = pd.date_range("2026-01-01", periods=2, tz="UTC")
    frame = pd.DataFrame({("Close", "DRIV"): [88.59, 88.59]}, index=index)
    frame.columns = pd.MultiIndex.from_tuples(frame.columns)
    assert _normalize(frame, "GM").empty


def test_cached_frames_are_copied_rather_than_mutated():
    namespace = "unit_symbol_copy_isolation"

    def loader():
        return _history([10, 11], [100, 110], "AAPL")

    first = cached_call(namespace, 60, loader)
    first.attrs["requested_symbol"] = "MUTATED"
    first.loc[first.index[-1], "Close"] = 999
    second = cached_call(namespace, 60, loader)
    assert second.attrs["requested_symbol"] == "AAPL"
    assert float(second["Close"].iloc[-1]) == 11.0


def test_etf_quote_cannot_be_reused_for_stock(monkeypatch):
    import market_data

    def wrong_snapshot(symbol):
        return MarketSnapshot(
            symbol="SPY",
            price=500,
            change_pct=1,
            volume=1000,
            timestamp="2026-01-02T20:00:00+00:00",
            provider="unit",
            requested_symbol="SPY",
            provider_symbol="SPY",
        )

    monkeypatch.setattr(market_data, "get_snapshot", wrong_snapshot)
    assert get_many_snapshots(["AAPL"], live=False) == {}


def test_duplicate_price_alone_does_not_quarantine_provider_results():
    snapshots = {
        symbol: MarketSnapshot(
            symbol=symbol,
            price=88.59,
            change_pct=0,
            volume=100,
            timestamp="2026-01-02T20:00:00+00:00",
            provider="unit",
            requested_symbol=symbol,
            provider_symbol=symbol,
        )
        for symbol in ("GM", "F", "AAPL")
    }
    assert _duplicate_price_quarantine(snapshots) == set()


def test_identical_cache_identity_quarantines_provider_results():
    snapshots = {
        symbol: MarketSnapshot(
            symbol=symbol,
            price=price,
            change_pct=0,
            volume=100,
            timestamp=datetime.now(timezone.utc).isoformat(),
            provider="unit",
            requested_symbol=symbol,
            provider_symbol=symbol,
            cache_identity="shared-cache",
            ohlcv_fingerprint="same-ohlcv",
        )
        for symbol, price in (("GM", 88.59), ("F", 162.0))
    }
    assert _duplicate_price_quarantine(snapshots) == {"GM", "F"}


def test_provider_error_logs_redact_api_keys():
    message = (
        "500 Server Error: https://eodhd.com/api/eod/AAPL.US?"
        "api_token=SECRET&fmt=json&apikey=ALSOSECRET&token=NOPE&key=BAD"
    )
    redacted = _redact_url(message)
    assert "SECRET" not in redacted
    assert "ALSOSECRET" not in redacted
    assert "api_token=REDACTED" in redacted
    assert "apikey=REDACTED" in redacted


def test_daily_date_only_index_retains_exchange_session_date():
    frame = pd.DataFrame(
        {"Close": [200.0], "Volume": [1000]},
        index=pd.DatetimeIndex(["2026-07-31"]),
    )
    verified = _verified_history(frame, "unit", "AAPL", "AAPL", "5d", "1d")
    assert str(verified.index[-1].date()) == "2026-07-31"
    assert verified.index.tz is None
