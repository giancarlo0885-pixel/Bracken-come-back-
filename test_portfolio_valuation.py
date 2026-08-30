from datetime import datetime, timezone

from market_data import MarketSnapshot
from portfolio_valuation import valuation_snapshot_is_safe


def _snapshot(**overrides):
    values = {
        "symbol": "XLF",
        "price": 58.10,
        "change_pct": 0.0,
        "volume": 1_000_000,
        "timestamp": "2026-08-28T20:00:00+00:00",
        "provider": "Yahoo Finance",
        "interval": "1d",
        "requested_symbol": "XLF",
        "provider_symbol": "XLF",
        "quote_verified": False,
        "stale": True,
        "source_capability": "history_daily",
        "source_identity": "Yahoo Finance:XLF:5d:1d",
    }
    values.update(overrides)
    return MarketSnapshot(**values)


def test_completed_close_can_mark_portfolio_without_becoming_execution_quote():
    saturday = datetime(2026, 8, 29, 18, 0, tzinfo=timezone.utc)
    snapshot = _snapshot()

    assert snapshot.quote_verified is False
    assert snapshot.stale is True
    assert valuation_snapshot_is_safe(snapshot, "XLF", now=saturday) is True
    assert snapshot.quote_verified is False
    assert snapshot.stale is True


def test_valuation_rejects_symbol_identity_mismatch():
    saturday = datetime(2026, 8, 29, 18, 0, tzinfo=timezone.utc)
    snapshot = _snapshot(provider_symbol="SPY")

    assert valuation_snapshot_is_safe(snapshot, "XLF", now=saturday) is False


def test_valuation_rejects_intraday_bar_as_official_close_mark():
    saturday = datetime(2026, 8, 29, 18, 0, tzinfo=timezone.utc)
    snapshot = _snapshot(interval="5m", timestamp="2026-08-28T19:55:00+00:00")

    assert valuation_snapshot_is_safe(snapshot, "XLF", now=saturday) is False


def test_valuation_rejects_old_completed_daily_bar():
    saturday = datetime(2026, 8, 29, 18, 0, tzinfo=timezone.utc)
    snapshot = _snapshot(timestamp="2026-08-27T20:00:00+00:00")

    assert valuation_snapshot_is_safe(snapshot, "XLF", now=saturday) is False
