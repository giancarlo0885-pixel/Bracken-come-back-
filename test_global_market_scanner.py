from __future__ import annotations

import sys
import types

# Lightweight psycopg stub for test environments where PostgreSQL drivers are absent.
if "psycopg" not in sys.modules:
    psycopg = types.ModuleType("psycopg")
    psycopg.Connection = object
    psycopg.connect = lambda *args, **kwargs: None
    rows = types.ModuleType("psycopg.rows")
    rows.dict_row = object()
    sys.modules["psycopg"] = psycopg
    sys.modules["psycopg.rows"] = rows

if "yfinance" not in sys.modules:
    yf = types.ModuleType("yfinance")
    yf.download = lambda *args, **kwargs: None
    sys.modules["yfinance"] = yf

import pandas as pd
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import global_market_scanner as scanner


def sample_history(symbol: str = "AAPL") -> pd.DataFrame:
    frame = pd.DataFrame({
        "Open": [100,101,102,103,104,105],
        "High": [102,103,104,105,107,111],
        "Low": [99,100,101,102,103,104],
        "Close": [101,102,103,104,106,110],
        "Volume": [1_000_000,1_050_000,1_100_000,1_000_000,1_200_000,2_500_000],
    }, index=pd.bdate_range("2026-07-24 20:00", periods=6, tz="UTC", normalize=False))
    frame.attrs["provider_route"] = {
        "provider": "Polygon",
        "requested_symbol": symbol,
        "provider_symbol": symbol,
        "quote_verified": True,
        "quote_timestamp": frame.index[-1].isoformat(),
        "interval": "1d",
    }
    return frame


def previous_session_history(symbol: str = "AAPL") -> pd.DataFrame:
    frame = sample_history(symbol).copy()
    return frame


def penny_history(symbol: str = "PENNY") -> pd.DataFrame:
    frame = sample_history(symbol).copy()
    frame["Close"] = [1.1, 1.15, 1.2, 1.25, 1.3, 1.4]
    frame["Volume"] = [1_000_000, 1_100_000, 1_200_000, 1_300_000, 1_400_000, 2_000_000]
    return frame


def test_yahoo_symbol_mapping():
    assert scanner._to_yahoo_symbol("AAPL", "US") == "AAPL"


def test_candidate_metrics_detects_liquid_mover(monkeypatch):
    monkeypatch.setattr(scanner, "get_history", lambda symbol, *args, **kwargs: sample_history(symbol))
    candidate = scanner._candidate_metrics(
        {"symbol":"AAPL","name":"Apple","exchange":"NASDAQ","region":"United States","sector":"Technology"},
        now=datetime(2026, 8, 1, 16, 0, tzinfo=timezone.utc),
    )
    assert candidate is not None
    assert candidate.mover_score > 0
    assert candidate.relative_volume > 1
    assert candidate.primary_category == "blue_chip"
    assert "major_gainer" in candidate.mover_tags


def test_seed_universe_is_us_crypto_scoped(monkeypatch):
    monkeypatch.setattr(scanner, "EODHD_API_KEY", "")
    universe = scanner._load_universe()
    regions = {item["region"] for item in universe}
    symbols = {item["symbol"] for item in universe}
    assert regions == {"United States"}
    assert not any(symbol.endswith((".DE", ".L", ".AX", ".NS", ".PA", ".AS")) for symbol in symbols)


def test_dynamic_universe_includes_core_etfs_and_size_categories(monkeypatch):
    monkeypatch.setattr(scanner, "EODHD_API_KEY", "")
    universe = scanner._load_universe()
    symbols = {item["symbol"] for item in universe}
    sectors = {item["sector"] for item in universe}
    assert {"GOOGL", "GOOG", "AMZN", "AAPL", "MSFT", "NVDA"}.issubset(symbols)
    assert "SPY" in symbols
    assert {"large_cap", "mid_cap", "small_cap", "qualified_penny"}.issubset(sectors)


def test_qualified_penny_stock_requires_strict_liquidity(monkeypatch):
    frame = sample_history("SOUN").copy()
    frame["Close"] = [1.1, 1.15, 1.2, 1.25, 1.3, 1.4]
    frame["Volume"] = [100, 100, 100, 100, 100, 100]
    monkeypatch.setattr(scanner, "get_history", lambda *args, **kwargs: frame)
    candidate = scanner._candidate_metrics({"symbol":"SOUN","name":"SOUN","exchange":"US","region":"United States","sector":"qualified_penny"})
    assert candidate is None


def test_blue_chip_can_also_be_major_mover(monkeypatch):
    monkeypatch.setattr(scanner, "get_history", lambda symbol, *args, **kwargs: sample_history(symbol))
    candidate = scanner._candidate_metrics(
        {"symbol":"AAPL","name":"Apple","exchange":"US","region":"United States","sector":"mega_cap_core"},
        now=datetime(2026, 8, 1, 16, 0, tzinfo=timezone.utc),
    )
    assert candidate is not None
    assert candidate.primary_category == "blue_chip"
    assert "major_gainer" in candidate.mover_tags


def test_expired_candidates_are_removed_from_active_list(monkeypatch):
    now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    old = (now - timedelta(seconds=scanner.GLOBAL_CANDIDATE_TTL_SECONDS + 1)).isoformat()
    fresh = (now - timedelta(seconds=10)).isoformat()
    records = [
        {"symbol": "OLD", "mover_score": 99, "scanned_at": old},
        {"symbol": "NEW", "mover_score": 10, "scanned_at": fresh},
    ]
    assert [item["symbol"] for item in scanner.filter_fresh_candidates(records, now)] == ["NEW"]


def test_provider_mover_discovery_uses_configured_alpha_capability(monkeypatch):
    class Settings:
        def get(self, name):
            return "key" if name == "ALPHA_VANTAGE_API_KEY" else None

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "top_gainers": [{"ticker": "GAIN"}],
                "top_losers": [{"ticker": "LOSE"}],
                "most_actively_traded": [{"ticker": "VOL"}],
            }

    monkeypatch.setattr(scanner, "get_api_settings", lambda: Settings())
    monkeypatch.setattr(scanner.requests, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(scanner, "cached_call", lambda namespace, ttl, fn, *args: fn(*args))
    symbols = {item["symbol"]: item["mover_type"] for item in scanner.provider_mover_universe()}
    assert symbols == {"GAIN": "major_gainer", "LOSE": "major_loser", "VOL": "unusual_volume"}
    [gain] = [item for item in scanner.provider_mover_universe() if item["symbol"] == "GAIN"]
    assert gain["quote_verified"] is False
    assert "provider_fetched_at" in gain
    assert "quote_timestamp" not in gain
    assert "market_session" not in gain


def test_eodhd_screener_without_quote_timestamp_is_discovery_only(monkeypatch):
    class Settings:
        def get(self, name):
            return "key" if name == "EODHD_API_KEY" else None

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"code": "GAIN.US", "exchange": "NASDAQ", "close": 12, "volume": 1_000_000}]}

    monkeypatch.setattr(scanner, "get_api_settings", lambda: Settings())
    monkeypatch.setattr(scanner.requests, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(scanner, "cached_call", lambda namespace, ttl, fn, *args: fn(*args))
    records = scanner.provider_mover_universe()
    assert records
    record = records[0]
    assert record["quote_verified"] is False
    assert "provider_fetched_at" in record
    assert "quote_timestamp" not in record
    assert "market_session" not in record


def test_provider_discovered_core_stock_keeps_mover_metadata(monkeypatch):
    merged = scanner.merge_candidate_metadata([
        {"symbol": "AAPL", "name": "Apple", "exchange": "NASDAQ", "region": "United States", "sector": "mega_cap_core"},
        {
                "symbol": "AAPL",
                "requested_symbol": "AAPL",
                "provider_symbol": "AAPL",
            "mover_type": "major_gainer",
            "discovery_source": "polygon_snapshot",
            "discovery_timestamp": "2026-08-01T12:00:00+00:00",
        },
    ])
    assert len(merged) == 1
    assert merged[0]["discovery_source"] == "polygon_snapshot"
    assert "major_gainer" in merged[0]["mover_tags"]

    monkeypatch.setattr(scanner, "get_history", lambda symbol, *args, **kwargs: sample_history(symbol))
    candidate = scanner._candidate_metrics(merged[0], now=datetime(2026, 8, 1, 16, 0, tzinfo=timezone.utc))
    assert candidate is not None
    assert candidate.primary_category == "blue_chip"
    assert "major_gainer" in candidate.mover_tags
    assert candidate.discovery_source == "polygon_snapshot"


def test_quote_freshness_uses_bar_time_not_fetch_time():
    now = datetime(2026, 8, 3, 16, 0, tzinfo=timezone.utc)
    old_bar = datetime(2026, 7, 31, 20, 0, tzinfo=timezone.utc)
    assert scanner.quote_is_fresh(old_bar.isoformat(), "1m", now, exchange="NASDAQ") is False


def test_fresh_intraday_bar_is_fresh():
    now = datetime(2026, 8, 3, 16, 0, tzinfo=timezone.utc)
    bar = now - timedelta(minutes=2)
    assert scanner.quote_is_fresh(bar.isoformat(), "1m", now, exchange="NASDAQ") is True


def test_friday_daily_bar_is_fresh_during_weekend():
    now = datetime(2026, 8, 1, 18, 0, tzinfo=timezone.utc)
    friday_close = datetime(2026, 7, 31, 20, 0, tzinfo=timezone.utc)
    assert scanner.quote_is_fresh(friday_close.isoformat(), "1d", now, exchange="NASDAQ") is True


def test_stale_daily_bar_is_not_fresh_during_open_session():
    now = datetime(2026, 8, 3, 16, 0, tzinfo=timezone.utc)
    friday_close = datetime(2026, 7, 31, 20, 0, tzinfo=timezone.utc)
    assert scanner.quote_is_fresh(friday_close.isoformat(), "1d", now, exchange="NASDAQ") is False


def test_candidate_metrics_rejects_stale_candidate(monkeypatch):
    stale = sample_history("AAPL")
    stale.index = pd.date_range("2026-07-20", periods=6, tz="UTC")
    monkeypatch.setattr(scanner, "get_history", lambda *args, **kwargs: stale)
    candidate = scanner._candidate_metrics(
        {"symbol": "AAPL", "name": "Apple", "exchange": "NASDAQ", "region": "United States", "sector": "mega_cap_core"},
        now=datetime(2026, 8, 1, 16, 0, tzinfo=timezone.utc),
    )
    assert candidate is None


def test_candidate_metrics_accepts_friday_daily_bar_during_saturday(monkeypatch):
    monkeypatch.setattr(scanner, "get_history", lambda symbol, *args, **kwargs: sample_history(symbol))
    candidate = scanner._candidate_metrics(
        {"symbol": "AAPL", "name": "Apple", "exchange": "NASDAQ", "region": "United States", "sector": "mega_cap_core"},
        now=datetime(2026, 8, 1, 16, 0, tzinfo=timezone.utc),
    )
    assert candidate is not None
    assert candidate.quote_timestamp.startswith("2026-07-31")


def test_candidate_metrics_rejects_previous_session_bar_after_monday_open(monkeypatch):
    monkeypatch.setattr(scanner, "get_history", lambda symbol, *args, **kwargs: sample_history(symbol))
    monkeypatch.setattr(scanner, "get_live_snapshot", lambda symbol: None)
    candidate = scanner._candidate_metrics(
        {"symbol": "AAPL", "name": "Apple", "exchange": "NASDAQ", "region": "United States", "sector": "mega_cap_core"},
        now=datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc),
    )
    assert candidate is None


def test_candidate_metrics_rejects_provider_discovered_otc_penny(monkeypatch):
    monkeypatch.setattr(scanner, "get_history", lambda symbol, *args, **kwargs: penny_history(symbol))
    candidate = scanner._candidate_metrics(
        {"symbol": "PENNY", "name": "Penny", "exchange": "OTCQB", "region": "United States", "sector": "major_gainer"},
        now=datetime(2026, 8, 1, 16, 0, tzinfo=timezone.utc),
    )
    assert candidate is None


def test_candidate_metrics_rejects_unknown_exchange_penny(monkeypatch):
    monkeypatch.setattr(scanner, "get_history", lambda symbol, *args, **kwargs: penny_history(symbol))
    candidate = scanner._candidate_metrics(
        {"symbol": "PENNY", "name": "Penny", "exchange": "", "region": "United States", "sector": "major_gainer"},
        now=datetime(2026, 8, 1, 16, 0, tzinfo=timezone.utc),
    )
    assert candidate is None


def test_one_ticker_retains_multiple_provider_mover_tags(monkeypatch):
    class Settings:
        def get(self, name):
            return "key" if name == "ALPHA_VANTAGE_API_KEY" else None

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "top_gainers": [{"ticker": "MIX", "exchange": "NASDAQ"}],
                "top_losers": [],
                "most_actively_traded": [{"ticker": "MIX", "exchange": "NASDAQ"}],
            }

    monkeypatch.setattr(scanner, "get_api_settings", lambda: Settings())
    monkeypatch.setattr(scanner.requests, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(scanner, "cached_call", lambda namespace, ttl, fn, *args: fn(*args))
    [record] = scanner.provider_mover_universe()
    assert record["symbol"] == "MIX"
    assert set(record["mover_tags"]) >= {"major_gainer", "unusual_volume"}
    assert record["exchange"] == "NASDAQ"


def test_provider_without_in_progress_daily_candle_uses_snapshot_during_regular_trading(monkeypatch):
    monkeypatch.setattr(scanner, "get_history", lambda symbol, *args, **kwargs: previous_session_history(symbol))
    candidate = scanner._candidate_metrics(
        {
            "symbol": "AAPL",
            "requested_symbol": "AAPL",
            "provider_symbol": "AAPL",
            "name": "Apple",
            "exchange": "NASDAQ",
            "region": "United States",
            "sector": "mega_cap_core",
            "price": 120,
            "change_1d_pct": 4.5,
            "daily_volume": 3_000_000,
            "quote_timestamp": "2026-08-03T14:01:00+00:00",
            "quote_provider": "polygon_snapshot",
            "market_session": "regular",
            "quote_verified": True,
        },
        now=datetime(2026, 8, 3, 14, 2, tzinfo=timezone.utc),
    )
    assert candidate is not None
    assert candidate.price == 120
    assert candidate.historical_bar_timestamp.startswith("2026-07-31")
    assert candidate.quote_provider == "polygon_snapshot"


def test_fresh_intraday_quote_plus_previous_session_daily_history(monkeypatch):
    monkeypatch.setattr(scanner, "get_history", lambda symbol, *args, **kwargs: previous_session_history(symbol))
    monkeypatch.setattr(
        scanner,
        "get_live_snapshot",
        lambda symbol: SimpleNamespace(
            price=121,
            change_pct=5.5,
            volume=4_000_000,
            timestamp="2026-08-03T14:03:00+00:00",
            interval="1m",
                provider="polygon_intraday",
                symbol="AAPL",
                requested_symbol="AAPL",
                provider_symbol="AAPL",
                quote_verified=True,
            ),
        )
    candidate = scanner._candidate_metrics(
        {"symbol": "AAPL", "name": "Apple", "exchange": "NASDAQ", "region": "United States", "sector": "mega_cap_core"},
        now=datetime(2026, 8, 3, 14, 4, tzinfo=timezone.utc),
    )
    assert candidate is not None
    assert candidate.price == 121
    assert candidate.quote_provider == "polygon_intraday"


def test_premarket_mover_uses_current_premarket_quote(monkeypatch):
    monkeypatch.setattr(scanner, "get_history", lambda symbol, *args, **kwargs: previous_session_history(symbol))
    candidate = scanner._candidate_metrics(
        {
                "symbol": "AAPL",
                "requested_symbol": "AAPL",
                "provider_symbol": "AAPL",
            "name": "Apple",
            "exchange": "NASDAQ",
            "region": "United States",
            "sector": "mega_cap_core",
            "price": 118,
            "change_1d_pct": 3.5,
            "daily_volume": 800_000,
            "quote_timestamp": "2026-08-03T12:00:00+00:00",
            "quote_provider": "polygon_premarket",
            "market_session": "premarket",
            "quote_verified": True,
        },
        now=datetime(2026, 8, 3, 12, 1, tzinfo=timezone.utc),
    )
    assert candidate is not None
    assert candidate.market_session == "premarket"
    assert "extended_hours" in candidate.mover_tags
    assert "extended" in candidate.risk_bucket


def test_after_hours_mover_uses_current_after_hours_quote(monkeypatch):
    monkeypatch.setattr(scanner, "get_history", lambda symbol, *args, **kwargs: previous_session_history(symbol))
    candidate = scanner._candidate_metrics(
        {
            "symbol": "AAPL",
            "requested_symbol": "AAPL",
            "provider_symbol": "AAPL",
            "name": "Apple",
            "exchange": "NASDAQ",
            "region": "United States",
            "sector": "mega_cap_core",
            "price": 116,
            "change_1d_pct": -3.2,
            "daily_volume": 900_000,
            "quote_timestamp": "2026-08-03T21:00:00+00:00",
            "quote_provider": "polygon_afterhours",
            "market_session": "after-hours",
            "quote_verified": True,
        },
        now=datetime(2026, 8, 3, 21, 1, tzinfo=timezone.utc),
    )
    assert candidate is not None
    assert candidate.market_session == "after-hours"
    assert "extended_hours" in candidate.mover_tags


def test_stale_intraday_quote_rejected(monkeypatch):
    monkeypatch.setattr(scanner, "get_history", lambda symbol, *args, **kwargs: previous_session_history(symbol))
    monkeypatch.setattr(
        scanner,
        "get_live_snapshot",
        lambda symbol: SimpleNamespace(
            price=121,
            change_pct=5.5,
            volume=4_000_000,
            timestamp="2026-08-03T13:00:00+00:00",
            interval="1m",
            provider="stale_intraday",
        ),
    )
    candidate = scanner._candidate_metrics(
        {"symbol": "AAPL", "name": "Apple", "exchange": "NASDAQ", "region": "United States", "sector": "mega_cap_core"},
        now=datetime(2026, 8, 3, 14, 4, tzinfo=timezone.utc),
    )
    assert candidate is None


def test_provider_snapshot_values_enter_mover_ranking(monkeypatch):
    monkeypatch.setattr(scanner, "get_history", lambda symbol, *args, **kwargs: previous_session_history(symbol))
    candidate = scanner._candidate_metrics(
        {
                "symbol": "GAIN",
                "requested_symbol": "GAIN",
                "provider_symbol": "GAIN",
            "name": "Gainer",
            "exchange": "NASDAQ",
            "region": "United States",
            "sector": "major_gainer",
            "price": 150,
            "change_1d_pct": 10,
            "daily_volume": 8_000_000,
            "relative_volume": 4,
            "quote_timestamp": "2026-08-03T14:05:00+00:00",
            "quote_provider": "alpha",
            "market_session": "regular",
            "quote_verified": True,
        },
        now=datetime(2026, 8, 3, 14, 6, tzinfo=timezone.utc),
    )
    assert candidate is not None
    assert candidate.price == 150
    assert candidate.change_1d_pct == 10
    assert candidate.relative_volume == 4
    assert candidate.mover_score > 50


def test_foreign_stock_candidates_are_out_of_us_crypto_scope(monkeypatch):
    hist = previous_session_history()
    hist.index = pd.bdate_range("2026-07-23 15:30", periods=6, tz="UTC", normalize=False)
    monkeypatch.setattr(scanner, "get_history", lambda *args, **kwargs: hist)
    candidate = scanner._candidate_metrics(
        {
                "symbol": "SAP.DE",
                "requested_symbol": "SAP.DE",
                "provider_symbol": "SAP.DE",
            "name": "SAP",
            "exchange": "XETRA",
            "region": "Europe",
            "sector": "Technology",
            "price": 125,
            "change_1d_pct": 4,
            "daily_volume": 2_000_000,
            "quote_timestamp": "2026-07-31T08:30:00+00:00",
            "quote_provider": "eodhd",
            "market_session": "regular",
            "quote_verified": True,
        },
        now=datetime(2026, 7, 31, 8, 31, tzinfo=timezone.utc),
    )
    assert candidate is None


def test_provider_fallback_when_current_quote_data_is_incomplete(monkeypatch):
    monkeypatch.setattr(scanner, "get_history", lambda symbol, *args, **kwargs: previous_session_history(symbol))
    monkeypatch.setattr(
        scanner,
        "get_live_snapshot",
        lambda symbol: SimpleNamespace(
            price=122,
            change_pct=6,
            volume=5_000_000,
            timestamp="2026-08-03T14:03:00+00:00",
            interval="1m",
                provider="fallback_intraday",
                symbol="AAPL",
                requested_symbol="AAPL",
                provider_symbol="AAPL",
                quote_verified=True,
            ),
        )
    candidate = scanner._candidate_metrics(
        {
                "symbol": "AAPL",
                "requested_symbol": "AAPL",
                "provider_symbol": "AAPL",
            "name": "Apple",
            "exchange": "NASDAQ",
            "region": "United States",
            "sector": "mega_cap_core",
            "price": 122,
            "change_1d_pct": None,
            "daily_volume": 5_000_000,
            "quote_timestamp": "2026-08-03T14:03:00+00:00",
            "quote_provider": "incomplete_provider",
            "market_session": "regular",
            "quote_verified": True,
        },
        now=datetime(2026, 8, 3, 14, 4, tzinfo=timezone.utc),
    )
    assert candidate is not None
    assert candidate.quote_provider == "fallback_intraday"


def test_provider_fetched_at_is_never_accepted_as_quote_timestamp(monkeypatch):
    monkeypatch.setattr(scanner, "get_history", lambda symbol, *args, **kwargs: previous_session_history(symbol))
    monkeypatch.setattr(scanner, "get_live_snapshot", lambda symbol: None)
    candidate = scanner._candidate_metrics(
        {
            "symbol": "AAPL",
            "name": "Apple",
            "exchange": "NASDAQ",
            "region": "United States",
            "sector": "mega_cap_core",
            "price": 122,
            "change_1d_pct": 6,
            "daily_volume": 5_000_000,
            "provider_fetched_at": "2026-08-03T14:03:00+00:00",
            "quote_provider": "alpha_vantage_top_gainers_losers",
            "quote_verified": False,
        },
        now=datetime(2026, 8, 3, 14, 4, tzinfo=timezone.utc),
    )
    assert candidate is None


def test_fresh_live_snapshot_upgrades_discovery_only_mover(monkeypatch):
    monkeypatch.setattr(scanner, "get_history", lambda symbol, *args, **kwargs: previous_session_history(symbol))
    monkeypatch.setattr(
        scanner,
        "get_live_snapshot",
        lambda symbol: SimpleNamespace(
            price=123,
            change_pct=7,
            volume=6_000_000,
            timestamp="2026-08-03T14:03:00+00:00",
            interval="1m",
                provider="live_snapshot",
                symbol="AAPL",
                requested_symbol="AAPL",
                provider_symbol="AAPL",
                quote_verified=True,
            ),
        )
    candidate = scanner._candidate_metrics(
        {
            "symbol": "AAPL",
            "name": "Apple",
            "exchange": "NASDAQ",
            "region": "United States",
            "sector": "mega_cap_core",
            "mover_tags": ["major_gainer"],
            "provider_fetched_at": "2026-08-03T14:02:00+00:00",
            "quote_verified": False,
        },
        now=datetime(2026, 8, 3, 14, 4, tzinfo=timezone.utc),
    )
    assert candidate is not None
    assert candidate.quote_provider == "live_snapshot"
    assert candidate.quote_verified is True
    assert candidate.quote_timestamp == "2026-08-03T14:03:00+00:00"
