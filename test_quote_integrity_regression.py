from __future__ import annotations

from datetime import datetime, timezone

import pytest

import market_data
from market_data import MarketSnapshot, get_live_snapshot
from profit_attribution import profit_attribution_rows


def _snapshot(symbol: str, price: float, *, verified: bool, provider: str) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        price=price,
        change_pct=0.0,
        volume=1_000.0,
        timestamp=datetime.now(timezone.utc).isoformat(),
        provider=provider,
        interval="5m",
        requested_symbol=symbol,
        provider_symbol=symbol,
        provider_native_symbol=symbol,
        quote_verified=verified,
        stale=not verified,
    )


def test_live_snapshot_skips_unverified_research_fallback_for_verified_quote(monkeypatch):
    calls: list[tuple[str, str]] = []
    snapshots = iter(
        [
            _snapshot("XLF", 56.95, verified=False, provider="Yahoo Finance"),
            _snapshot("XLF", 58.10, verified=True, provider="Polygon"),
        ]
    )

    def fake_history(symbol: str, period: str, interval: str):
        calls.append((period, interval))
        return object()

    monkeypatch.setattr(market_data, "get_history", fake_history)
    monkeypatch.setattr(market_data, "_snapshot_from_history", lambda symbol, history, interval: next(snapshots))
    monkeypatch.setattr(market_data, "snapshot_is_verified", lambda snapshot, symbol: bool(snapshot and snapshot.quote_verified))

    result = get_live_snapshot("XLF")

    assert result is not None
    assert result.quote_verified is True
    assert result.price == pytest.approx(58.10)
    assert calls == [("1d", "1m"), ("5d", "5m")]


def test_unverified_open_position_does_not_report_false_zero_pnl():
    rows = profit_attribution_rows(
        positions=[
            {
                "symbol": "XLF",
                "market": "cash",
                "quantity": 62_739.75,
                "average_price": 56.94,
                "current_price": 56.95,
                "quote_verified": False,
            }
        ],
        ledger_rows=[],
        market="cash",
        equity=65_400_000.0,
    )

    assert len(rows) == 1
    assert rows[0]["status"] == "WAITING FOR VERIFIED PRICE"
    assert rows[0]["unrealized_pnl"] is None
    assert rows[0]["total_pnl"] is None
    assert rows[0]["return_pct"] is None
    assert rows[0]["contribution_to_portfolio_profit_pct"] is None


def test_verified_xlf_quote_marks_position_to_market():
    quantity = 62_739.75
    entry = 56.94
    price = 58.10
    rows = profit_attribution_rows(
        positions=[
            {
                "symbol": "XLF",
                "market": "cash",
                "quantity": quantity,
                "average_price": entry,
                "current_price": 56.95,
                "quote_verified": False,
            }
        ],
        ledger_rows=[],
        quotes={
            "XLF": {
                "symbol": "XLF",
                "requested_symbol": "XLF",
                "provider_symbol": "XLF",
                "price": price,
                "quote_verified": True,
            }
        },
        market="cash",
        equity=65_400_000.0,
    )

    expected_pnl = (price - entry) * quantity
    assert rows[0]["status"] == "VERIFIED"
    assert rows[0]["exit_or_current_price"] == pytest.approx(price)
    assert rows[0]["unrealized_pnl"] == pytest.approx(expected_pnl)
    assert rows[0]["total_pnl"] == pytest.approx(expected_pnl)
    assert rows[0]["return_pct"] == pytest.approx(((price / entry) - 1.0) * 100.0)
