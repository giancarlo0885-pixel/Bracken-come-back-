from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

import market_data
from market_data import MarketSnapshot
from portfolio_advisor import analyze_portfolio
from provider_router import RoutedHistory


def _fresh_yahoo_frame(symbol: str = "BTC-USD") -> pd.DataFrame:
    now = datetime.now(timezone.utc)
    frame = pd.DataFrame(
        {
            "Open": [77_700.0, 77_790.0],
            "High": [77_850.0, 77_900.0],
            "Low": [77_650.0, 77_760.0],
            "Close": [77_790.0, 77_805.0],
            "Volume": [100.0, 120.0],
        },
        index=pd.DatetimeIndex([now - timedelta(minutes=5), now - timedelta(seconds=30)]),
    )
    frame.attrs.update(
        {
            "requested_symbol": symbol,
            "provider_symbol": symbol,
            "provider_native_symbol": symbol,
            "provider": "Yahoo Finance",
            "period": "5d",
            "interval": "5m",
            "quote_verified": False,
            "source_mode": "strict_research_fallback",
            "source_identity": f"Yahoo Finance:{symbol}:5d:5m",
        }
    )
    return frame


def _routed(frame: pd.DataFrame) -> RoutedHistory:
    return RoutedHistory(
        frame=frame,
        provider="Yahoo Finance",
        attempts=[],
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


def test_fresh_yahoo_intraday_can_mark_paper_portfolio(monkeypatch):
    frame = _fresh_yahoo_frame()
    monkeypatch.setattr(market_data, "PAPER_BROKER_MODE", True)
    monkeypatch.setattr(market_data, "EXECUTION_MODE", "paper")
    monkeypatch.setattr(market_data, "route_history", lambda *args, **kwargs: _routed(frame))

    history = market_data.get_history("BTC-USD", "5d", "5m")
    route = history.attrs["provider_route"]

    assert route["quote_verified"] is True
    assert route["provider_quote_verified"] is False
    assert route["paper_reference_verified"] is True
    assert route["verification_basis"] == "paper:fresh_identity_matched_yahoo"

    snapshot = market_data._snapshot_from_history("BTC-USD", history, "5m")
    assert snapshot is not None
    assert snapshot.price == 77_805.0
    assert snapshot.provider_quote_verified is False
    assert snapshot.paper_reference_verified is True
    assert market_data.snapshot_is_verified(snapshot, "BTC-USD") is True


def test_yahoo_paper_fallback_never_promotes_live_broker_mode(monkeypatch):
    frame = _fresh_yahoo_frame()
    monkeypatch.setattr(market_data, "PAPER_BROKER_MODE", True)
    monkeypatch.setattr(market_data, "EXECUTION_MODE", "live")
    monkeypatch.setattr(market_data, "route_history", lambda *args, **kwargs: _routed(frame))

    history = market_data.get_history("BTC-USD", "5d", "5m")
    route = history.attrs["provider_route"]

    assert route["quote_verified"] is False
    assert route["provider_quote_verified"] is False
    assert route["paper_reference_verified"] is False


def test_verified_five_minute_crypto_snapshot_uses_decision_freshness_window(monkeypatch):
    monkeypatch.setattr(market_data, "DECISION_CRYPTO_MAX_AGE_MINUTES", 45)
    now = datetime.now(timezone.utc)
    snapshot = MarketSnapshot(
        symbol="BTC-USD",
        price=77_805.0,
        change_pct=0.0,
        volume=100.0,
        timestamp=(now - timedelta(minutes=4)).isoformat(),
        provider="Polygon",
        interval="5m",
        requested_symbol="BTC-USD",
        provider_symbol="BTC-USD",
        provider_native_symbol="X:BTCUSD",
        quote_verified=True,
        stale=False,
        provider_quote_verified=True,
        verification_basis="provider",
    )

    assert market_data.snapshot_is_verified(snapshot, "BTC-USD") is True


def test_empty_portfolio_is_not_reported_as_balanced():
    health = analyze_portfolio(
        cash=5_000_000.0,
        positions=[],
        margin_debt=0.0,
        leverage_limit=2.0,
    )

    assert health.position_count == 0
    assert "no capital is currently deployed" in health.plain_summary.lower()
    assert health.plain_summary != "Portfolio structure is balanced."
