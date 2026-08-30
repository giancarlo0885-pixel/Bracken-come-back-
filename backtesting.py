from __future__ import annotations

import math

import numpy as np
import pandas as pd

from engine import analyze_market
from paper_execution_reality import simulate_fill


def _max_drawdown(curve: list[float]) -> float:
    if not curve:
        return 0.0
    arr = np.asarray(curve, float)
    peaks = np.maximum.accumulate(arr)
    dd = np.where(peaks > 0, (arr - peaks) / peaks, 0)
    return float(dd.min() * 100)


def _sharpe(returns: pd.Series) -> float:
    returns = returns.dropna()
    if len(returns) < 2 or returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * math.sqrt(252))


def _number(value: object, fallback: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    return result if math.isfinite(result) else fallback


def _bar_value(bar: pd.Series, name: str, fallback: float) -> float:
    value = _number(bar.get(name), fallback)
    return value if value > 0 else fallback


def _quote_for_bar(bar: pd.Series, reference_price: float, spread_pct: float) -> dict[str, float]:
    volume = max(0.0, _number(bar.get("Volume"), 0.0))
    return {
        "price": reference_price,
        "volume": volume,
        "liquidity_value": volume * reference_price if volume > 0 else 0.0,
        "spread_pct": max(0.0, spread_pct),
    }


def run_backtest(
    symbol: str,
    history: pd.DataFrame,
    starting_cash: float = 2000.0,
    fee_bps: float = 5.0,
    slippage_bps: float = 5.0,
    stop_loss_pct: float = 0.08,
    take_profit_pct: float = 0.16,
    spread_bps: float = 2.0,
    latency_bps: float = 1.0,
) -> dict:
    """Walk-forward backtest with next-bar execution and shared fill realism.

    A decision is generated only from bars that were complete before the current
    execution bar. The resulting order fills at the next bar's open (or a
    conservative stop reference) through the same adverse fill model used by
    forward paper trading. This removes the previous same-close look-ahead bias.
    """
    if history is None or history.empty or len(history) < 120:
        return {"error": "Not enough data"}
    if "Close" not in history.columns:
        return {"error": "Close column is required"}

    market = "crypto" if str(symbol or "").upper().endswith("-USD") else "cash"
    cash = float(starting_cash)
    qty = 0.0
    entry = 0.0
    trades: list[dict] = []
    curve: list[float] = []
    dates: list[str] = []
    fee_pct = max(0.0, float(fee_bps)) / 10_000.0
    slippage_pct = max(0.0, float(slippage_bps)) / 10_000.0
    spread_pct = max(0.0, float(spread_bps)) / 10_000.0
    latency_pct = max(0.0, float(latency_bps)) / 10_000.0

    # i is the execution bar. signal_window ends at i-1, so no data from the
    # execution bar can influence the decision that trades at its open.
    for i in range(60, len(history)):
        signal_window = history.iloc[:i]
        bar = history.iloc[i]
        previous_close = _bar_value(signal_window.iloc[-1], "Close", 0.0)
        open_price = _bar_value(bar, "Open", previous_close)
        close_price = _bar_value(bar, "Close", open_price)
        high_price = _bar_value(bar, "High", max(open_price, close_price))
        low_price = _bar_value(bar, "Low", min(open_price, close_price))
        high_price = max(high_price, open_price, close_price)
        low_price = min(low_price, open_price, close_price)
        dt = str(history.index[i])
        signal_dt = str(history.index[i - 1])

        signal = analyze_market(symbol, signal_window, 0.0)
        action = str(getattr(signal, "action", "HOLD") if signal is not None else "HOLD").upper()
        exited = False

        # Protective exits are evaluated using only the current bar's observable
        # range. If both stop and target are touched in one bar, assume the stop
        # happened first; this avoids choosing the favorable unknown sequence.
        if qty > 0 and entry > 0:
            stop_price = entry * (1.0 - stop_loss_pct)
            target_price = entry * (1.0 + take_profit_pct)
            exit_reference = 0.0
            exit_reason = ""
            if low_price <= stop_price:
                exit_reference = min(open_price, stop_price)
                exit_reason = "stop_loss"
            elif high_price >= target_price:
                exit_reference = target_price
                exit_reason = "take_profit"

            if exit_reference > 0:
                notional = qty * exit_reference
                fill = simulate_fill(
                    side="SELL",
                    market=market,
                    reference_price=exit_reference,
                    quote=_quote_for_bar(bar, exit_reference, spread_pct),
                    order_value=notional,
                    slippage_pct=slippage_pct,
                    fee_pct=fee_pct,
                    spread_pct=spread_pct,
                    latency_pct=latency_pct,
                )
                cash = qty * fill.fill_price
                trades.append(
                    {
                        "signal_date": signal_dt,
                        "date": dt,
                        "side": "SELL",
                        "reference_price": exit_reference,
                        "price": fill.fill_price,
                        "quantity": qty,
                        "reason": exit_reason,
                        "adverse_cost_pct": fill.adverse_cost_pct,
                    }
                )
                qty = 0.0
                entry = 0.0
                exited = True

        if not exited and qty == 0 and action == "BUY" and open_price > 0:
            fill = simulate_fill(
                side="BUY",
                market=market,
                reference_price=open_price,
                quote=_quote_for_bar(bar, open_price, spread_pct),
                order_value=cash,
                slippage_pct=slippage_pct,
                fee_pct=fee_pct,
                spread_pct=spread_pct,
                latency_pct=latency_pct,
            )
            qty = cash / fill.fill_price if fill.fill_price > 0 else 0.0
            if qty > 0:
                entry = fill.fill_price
                cash = 0.0
                trades.append(
                    {
                        "signal_date": signal_dt,
                        "date": dt,
                        "side": "BUY",
                        "reference_price": open_price,
                        "price": fill.fill_price,
                        "quantity": qty,
                        "reason": "signal_next_bar_open",
                        "adverse_cost_pct": fill.adverse_cost_pct,
                    }
                )
        elif not exited and qty > 0 and action == "SELL" and open_price > 0:
            notional = qty * open_price
            fill = simulate_fill(
                side="SELL",
                market=market,
                reference_price=open_price,
                quote=_quote_for_bar(bar, open_price, spread_pct),
                order_value=notional,
                slippage_pct=slippage_pct,
                fee_pct=fee_pct,
                spread_pct=spread_pct,
                latency_pct=latency_pct,
            )
            cash = qty * fill.fill_price
            trades.append(
                {
                    "signal_date": signal_dt,
                    "date": dt,
                    "side": "SELL",
                    "reference_price": open_price,
                    "price": fill.fill_price,
                    "quantity": qty,
                    "reason": "signal_next_bar_open",
                    "adverse_cost_pct": fill.adverse_cost_pct,
                }
            )
            qty = 0.0
            entry = 0.0

        curve.append(cash + qty * close_price)
        dates.append(dt)

    if qty > 0:
        final_bar = history.iloc[-1]
        reference = _bar_value(final_bar, "Close", entry)
        notional = qty * reference
        fill = simulate_fill(
            side="SELL",
            market=market,
            reference_price=reference,
            quote=_quote_for_bar(final_bar, reference, spread_pct),
            order_value=notional,
            slippage_pct=slippage_pct,
            fee_pct=fee_pct,
            spread_pct=spread_pct,
            latency_pct=latency_pct,
        )
        cash = qty * fill.fill_price
        trades.append(
            {
                "signal_date": str(history.index[-1]),
                "date": str(history.index[-1]),
                "side": "SELL",
                "reference_price": reference,
                "price": fill.fill_price,
                "quantity": qty,
                "reason": "end_of_test_liquidation",
                "adverse_cost_pct": fill.adverse_cost_pct,
            }
        )
        qty = 0.0
        if curve:
            curve[-1] = cash

    final = float(curve[-1] if curve else starting_cash)
    series = pd.Series(curve, index=pd.to_datetime(dates))
    daily = series.pct_change()
    buys = sum(t["side"] == "BUY" for t in trades)
    sells = sum(t["side"] == "SELL" for t in trades)
    return {
        "symbol": symbol,
        "strategy": "oracle_council_v3",
        "execution_model": "next_bar_open_with_adverse_fill",
        "starting_cash": starting_cash,
        "ending_equity": round(final, 2),
        "net_profit": round(final - starting_cash, 2),
        "return_pct": round((final / starting_cash - 1) * 100, 2),
        "max_drawdown_pct": round(_max_drawdown(curve), 2),
        "sharpe_ratio": round(_sharpe(daily), 3),
        "trades": len(trades),
        "round_trips": min(buys, sells),
        "fee_bps": fee_bps,
        "slippage_bps": slippage_bps,
        "spread_bps": spread_bps,
        "latency_bps": latency_bps,
        "equity_curve": curve,
        "dates": dates,
        "trade_log": trades,
    }
