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
import provider_router
from provider_router import _alpha, _eodhd, _redact_url, _verified_history


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


def test_snapshot_does_not_fabricate_quote_timestamp_from_fetched_at():
    history = _history([100, 101], [1000, 1100], "AAPL")
    history.index = pd.Index(["bad-index-1", "bad-index-2"])
    history.attrs["provider_route"] = {
        "provider": "unit",
        "requested_symbol": "AAPL",
        "provider_symbol": "AAPL",
        "fetched_at": "2026-08-01T12:00:00+00:00",
        "interval": "5m",
        "quote_verified": True,
    }

    assert _snapshot_from_history("AAPL", history, "5m") is None


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


def test_market_snapshot_defaults_quote_verified_false():
    snapshot = MarketSnapshot("AAPL", 100, 0, 1000, "2026-07-31T20:00:00+00:00")
    assert snapshot.quote_verified is False


def test_two_yahoo_symbols_are_not_quarantined_by_provider_only_identity():
    snapshots = {
        "AAPL": MarketSnapshot(
            "AAPL",
            100,
            0,
            1000,
            datetime.now(timezone.utc).isoformat(),
            provider="Yahoo Finance",
            requested_symbol="AAPL",
            provider_symbol="AAPL",
            quote_verified=True,
            source_identity="Yahoo Finance:AAPL:5d:1d",
        ),
        "MSFT": MarketSnapshot(
            "MSFT",
            100,
            0,
            1000,
            datetime.now(timezone.utc).isoformat(),
            provider="Yahoo Finance",
            requested_symbol="MSFT",
            provider_symbol="MSFT",
            quote_verified=True,
            source_identity="Yahoo Finance:MSFT:5d:1d",
        ),
    }
    assert _duplicate_price_quarantine(snapshots) == set()


def test_eodhd_daily_dates_retain_original_session_date(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [{"date": "2026-07-31", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 100}]

    monkeypatch.setattr(provider_router, "_eodhd_symbol_mapping", lambda symbol, key: {"requested_symbol": "AAPL", "provider_code": "AAPL.US", "exchange": "NASDAQ", "instrument_type": "common stock"})
    monkeypatch.setattr(provider_router.requests, "get", lambda *args, **kwargs: Response())
    frame = _eodhd("AAPL", "5d", "1d", "key")
    assert str(frame.index[-1].date()) == "2026-07-31"
    assert frame.index.tz is None
    assert frame.attrs["quote_verified"] is True


def test_eodhd_verified_us_mapping(monkeypatch):
    records = [{"Code": "AAPL", "ProviderCode": "AAPL.US", "Exchange": "NASDAQ", "Type": "Common Stock"}]
    monkeypatch.setattr(provider_router, "cached_call", lambda *args, **kwargs: records)
    mapping = provider_router._eodhd_symbol_mapping("AAPL", "key")
    assert mapping["provider_code"] == "AAPL.US"
    assert mapping["exchange"] == "NASDAQ"


def test_eodhd_verified_etf_mapping(monkeypatch):
    records = [{"Code": "SPY", "ProviderCode": "SPY.US", "Exchange": "NYSE ARCA", "Type": "ETF"}]
    monkeypatch.setattr(provider_router, "cached_call", lambda *args, **kwargs: records)
    mapping = provider_router._eodhd_symbol_mapping("SPY", "key")
    assert mapping["provider_code"] == "SPY.US"
    assert mapping["instrument_type"] == "etf"


def test_eodhd_missing_exchange_rejected(monkeypatch):
    records = [{"Code": "AAPL", "ProviderCode": "AAPL.US", "Type": "Common Stock"}]
    monkeypatch.setattr(provider_router, "cached_call", lambda *args, **kwargs: records)
    assert provider_router._eodhd_symbol_mapping("AAPL", "key") is None


def test_eodhd_missing_type_rejected(monkeypatch):
    records = [{"Code": "AAPL", "ProviderCode": "AAPL.US", "Exchange": "NASDAQ"}]
    monkeypatch.setattr(provider_router, "cached_call", lambda *args, **kwargs: records)
    assert provider_router._eodhd_symbol_mapping("AAPL", "key") is None


def test_eodhd_missing_mapping_rejected(monkeypatch):
    monkeypatch.setattr(provider_router, "cached_call", lambda *args, **kwargs: [])
    assert provider_router._eodhd_symbol_mapping("AAPL", "key") is None


def test_eodhd_mismatched_provider_code_rejected(monkeypatch):
    records = [{"Code": "AAPL", "ProviderCode": "MSFT.US", "Exchange": "NASDAQ", "Type": "Common Stock"}]
    monkeypatch.setattr(provider_router, "cached_call", lambda *args, **kwargs: records)
    assert provider_router._eodhd_symbol_mapping("AAPL", "key") is None


def test_eodhd_mismatched_exchange_rejected(monkeypatch):
    records = [{"Code": "AAPL", "ProviderCode": "AAPL.US", "Exchange": "LSE", "Type": "Common Stock"}]
    monkeypatch.setattr(provider_router, "cached_call", lambda *args, **kwargs: records)
    assert provider_router._eodhd_symbol_mapping("AAPL", "key") is None


def test_eodhd_unsupported_type_rejected(monkeypatch):
    records = [{"Code": "AAPL", "ProviderCode": "AAPL.US", "Exchange": "NASDAQ", "Type": "Bond"}]
    monkeypatch.setattr(provider_router, "cached_call", lambda *args, **kwargs: records)
    assert provider_router._eodhd_symbol_mapping("AAPL", "key") is None


def test_eodhd_unsupported_foreign_suffix_rejected(monkeypatch):
    monkeypatch.setattr(provider_router, "cached_call", lambda *args, **kwargs: [{"Code": "SHEL"}])
    assert provider_router._eodhd_symbol_mapping("SHEL.L", "key") is None


def test_nonempty_eodhd_data_without_verified_identity_remains_unverified():
    frame = pd.DataFrame({"Close": [100.0], "Volume": [1000]}, index=pd.DatetimeIndex(["2026-07-31"]))
    result = provider_router._verified_history(frame, "EODHD", "AAPL", "AAPL", "5d", "1d")
    assert result.empty is False
    assert result.attrs["quote_verified"] is False


def test_alpha_vantage_daily_dates_retain_original_session_date(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "Meta Data": {"2. Symbol": "AAPL"},
                "Time Series (Daily)": {
                    "2026-07-31": {
                        "1. open": "1",
                        "2. high": "2",
                        "3. low": "1",
                        "5. adjusted close": "2",
                        "6. volume": "100",
                    }
                }
            }

    import alpha_vantage_provider
    monkeypatch.setattr(alpha_vantage_provider.requests, "get", lambda *args, **kwargs: Response())
    frame = _alpha("AAPL", "5d", "1d", "key")
    assert str(frame.index[-1].date()) == "2026-07-31"
    assert frame.index.tz is None


def test_foreign_alpha_vantage_intraday_uses_provider_timezone(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "Meta Data": {"2. Symbol": "VOD.L", "6. Time Zone": "Europe/London"},
                "Time Series (5min)": {
                    "2026-07-31 08:00:00": {
                        "1. open": "1",
                        "2. high": "2",
                        "3. low": "1",
                        "4. close": "2",
                        "5. volume": "100",
                    }
                },
            }

    monkeypatch.setattr(provider_router, "ALPHA_VANTAGE_PREMIUM", True)
    monkeypatch.setattr(provider_router.requests, "get", lambda *args, **kwargs: Response())
    frame = _alpha("VOD.L", "1d", "5m", "key")
    assert frame.index[-1].isoformat() == "2026-07-31T07:00:00+00:00"


def test_missing_alpha_vantage_intraday_timezone_is_rejected(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "Meta Data": {},
                "Time Series (5min)": {
                    "2026-07-31 08:00:00": {
                        "1. open": "1",
                        "2. high": "2",
                        "3. low": "1",
                        "4. close": "2",
                        "5. volume": "100",
                    }
                },
            }

    monkeypatch.setattr(provider_router.requests, "get", lambda *args, **kwargs: Response())
    assert _alpha("VOD.L", "1d", "5m", "key").empty
