from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

import backtesting
from paper_execution_reality import simulate_fill


def test_buy_pays_ask_and_sell_receives_bid_when_other_costs_zero():
    quote = {"bid": 99.0, "ask": 101.0, "spread_pct": 0.02}
    buy = simulate_fill(
        side="BUY",
        market="cash",
        reference_price=100.0,
        quote=quote,
        slippage_pct=0.0,
        fee_pct=0.0,
        market_impact_pct=0.0,
        latency_pct=0.0,
    )
    sell = simulate_fill(
        side="SELL",
        market="cash",
        reference_price=100.0,
        quote=quote,
        slippage_pct=0.0,
        fee_pct=0.0,
        market_impact_pct=0.0,
        latency_pct=0.0,
    )
    assert buy.fill_price == 101.0
    assert sell.fill_price == 99.0


def test_default_paper_fill_is_never_better_than_reference():
    buy = simulate_fill(side="BUY", market="cash", reference_price=100.0, quote={})
    sell = simulate_fill(side="SELL", market="cash", reference_price=100.0, quote={})
    assert buy.fill_price > 100.0
    assert sell.fill_price < 100.0
    assert buy.adverse_cost_pct > 0
    assert sell.adverse_cost_pct > 0


def test_market_impact_increases_with_participation():
    quote = {"volume": 1_000.0, "spread_pct": 0.001}
    small = simulate_fill(
        side="BUY",
        market="cash",
        reference_price=100.0,
        quote=quote,
        order_value=100.0,
        slippage_pct=0.0,
        fee_pct=0.0,
        latency_pct=0.0,
    )
    large = simulate_fill(
        side="BUY",
        market="cash",
        reference_price=100.0,
        quote=quote,
        order_value=50_000.0,
        slippage_pct=0.0,
        fee_pct=0.0,
        latency_pct=0.0,
    )
    assert small.participation_rate is not None
    assert large.participation_rate is not None
    assert large.participation_rate > small.participation_rate
    assert large.market_impact_pct > small.market_impact_pct
    assert large.fill_price > small.fill_price


@dataclass
class _Signal:
    action: str


def test_backtest_executes_signal_on_next_bar_open(monkeypatch):
    dates = pd.date_range("2025-01-01", periods=130, freq="D")
    base = [100.0 + i * 0.05 for i in range(130)]
    history = pd.DataFrame(
        {
            "Open": base,
            "High": [value + 0.25 for value in base],
            "Low": [value - 0.25 for value in base],
            "Close": [value + 0.10 for value in base],
            "Volume": [1_000_000.0] * 130,
        },
        index=dates,
    )

    def fake_analyze_market(symbol, window, _sentiment):
        return _Signal("BUY" if len(window) == 60 else "HOLD")

    monkeypatch.setattr(backtesting, "analyze_market", fake_analyze_market)
    result = backtesting.run_backtest(
        "AAPL",
        history,
        fee_bps=0.0,
        slippage_bps=0.0,
        spread_bps=0.0,
        latency_bps=0.0,
        stop_loss_pct=0.90,
        take_profit_pct=9.0,
    )

    first_buy = next(trade for trade in result["trade_log"] if trade["side"] == "BUY")
    assert first_buy["signal_date"] == str(dates[59])
    assert first_buy["date"] == str(dates[60])
    assert first_buy["reference_price"] == history.iloc[60]["Open"]
    assert result["execution_model"] == "next_bar_open_with_adverse_fill"
