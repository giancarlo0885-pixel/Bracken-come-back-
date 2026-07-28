from __future__ import annotations
import math
import numpy as np
import pandas as pd
from engine import analyze_market

def _max_drawdown(curve: list[float]) -> float:
    if not curve: return 0.0
    arr=np.asarray(curve,float); peaks=np.maximum.accumulate(arr)
    dd=np.where(peaks>0,(arr-peaks)/peaks,0)
    return float(dd.min()*100)

def _sharpe(returns: pd.Series) -> float:
    returns=returns.dropna()
    if len(returns)<2 or returns.std()==0: return 0.0
    return float(returns.mean()/returns.std()*math.sqrt(252))

def run_backtest(symbol: str, history: pd.DataFrame, starting_cash=2000.0,
                 fee_bps: float=5.0, slippage_bps: float=5.0,
                 stop_loss_pct: float=0.08, take_profit_pct: float=0.16) -> dict:
    if history is None or history.empty or len(history)<120: return {"error":"Not enough data"}
    cash=float(starting_cash); qty=0.0; entry=0.0; trades=[]; curve=[]; dates=[]
    friction=(fee_bps+slippage_bps)/10000
    for i in range(60,len(history)):
        window=history.iloc[:i+1]; price=float(window["Close"].iloc[-1]); dt=str(window.index[-1])
        signal=analyze_market(symbol,window,0.0)
        exit_reason=None
        if qty>0 and entry>0:
            if price<=entry*(1-stop_loss_pct): exit_reason="stop_loss"
            elif price>=entry*(1+take_profit_pct): exit_reason="take_profit"
        if qty==0 and signal and signal.action=="BUY":
            buy_price=price*(1+friction); qty=cash/buy_price; cash=0; entry=buy_price
            trades.append({"date":dt,"side":"BUY","price":buy_price,"quantity":qty,"reason":"signal"})
        elif qty>0 and ((signal and signal.action=="SELL") or exit_reason):
            sell_price=price*(1-friction); cash=qty*sell_price
            trades.append({"date":dt,"side":"SELL","price":sell_price,"quantity":qty,"reason":exit_reason or "signal"})
            qty=0; entry=0
        curve.append(cash+qty*price); dates.append(dt)
    if qty>0:
        price=float(history["Close"].iloc[-1]); cash=qty*price*(1-friction); qty=0; curve[-1]=cash
    final=float(curve[-1] if curve else starting_cash)
    series=pd.Series(curve,index=pd.to_datetime(dates)); daily=series.pct_change()
    buys=sum(t["side"]=="BUY" for t in trades); sells=sum(t["side"]=="SELL" for t in trades)
    return {"symbol":symbol,"strategy":"oracle_council_v3","starting_cash":starting_cash,
            "ending_equity":round(final,2),"net_profit":round(final-starting_cash,2),
            "return_pct":round((final/starting_cash-1)*100,2),"max_drawdown_pct":round(_max_drawdown(curve),2),
            "sharpe_ratio":round(_sharpe(daily),3),"trades":len(trades),"round_trips":min(buys,sells),
            "fee_bps":fee_bps,"slippage_bps":slippage_bps,"equity_curve":curve,"dates":dates,"trade_log":trades}
