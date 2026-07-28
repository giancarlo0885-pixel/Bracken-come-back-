from __future__ import annotations
import pandas as pd

def detect_regime(history: pd.DataFrame) -> dict:
    if history.empty or len(history)<60:
        return {"name":"unknown","score":0.0,"description":"Not enough data."}
    close=history["Close"].astype(float)
    sma20=close.tail(20).mean()
    sma60=close.tail(60).mean()
    vol=close.pct_change().tail(20).std()
    trend=(sma20/sma60)-1 if sma60 else 0
    if trend>0.04 and vol<0.025:
        name="risk-on"
    elif trend<-0.04:
        name="risk-off"
    elif vol>0.04:
        name="high-volatility"
    else:
        name="range-bound"
    return {"name":name,"score":float(trend),"description":f"20/60 trend {trend:+.1%}; daily volatility {vol:.1%}."}
