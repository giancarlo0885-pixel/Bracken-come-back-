from __future__ import annotations
from dataclasses import dataclass
import numpy as np, pandas as pd

@dataclass
class Forecast:
    horizon_days: int
    target_price: float
    low_price: float
    high_price: float
    probability_up: float
    model: str

def forecast_price(history: pd.DataFrame, days=5) -> Forecast | None:
    if history.empty or len(history)<40:
        return None
    close=history["Close"].astype(float).dropna()
    spot=float(close.iloc[-1])
    returns=np.log(close/close.shift(1)).dropna()
    recent=returns.tail(60)
    drift=float(recent.mean())
    vol=float(recent.std())
    target=spot*np.exp((drift-0.5*vol**2)*days)
    radius=vol*np.sqrt(days)
    low=target*np.exp(-1.645*radius)
    high=target*np.exp(1.645*radius)
    z=drift*np.sqrt(days)/(vol+1e-12)
    probability_up=float(1/(1+np.exp(-1.7*z)))
    return Forecast(days,float(target),float(low),float(high),probability_up,"log-return diffusion")
