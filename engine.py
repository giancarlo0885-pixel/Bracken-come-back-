from __future__ import annotations
from dataclasses import dataclass, asdict
import math, numpy as np, pandas as pd
from config import SIGNAL_BUY_THRESHOLD, SIGNAL_SELL_THRESHOLD
from crypto_mean_reversion import assess_short_horizon_mean_reversion
from technical_indicators import rsi, macd, atr, bollinger_position
from regime import detect_regime

@dataclass
class OracleSignal:
    symbol:str; price:float; score:float; action:str; confidence:float
    momentum_5d:float; momentum_20d:float; rsi_14:float; volatility_20d:float
    trend_strength:float; volume_ratio:float; news_sentiment:float
    macd_hist:float; atr_pct:float; bollinger_position:float; regime:str; reason:str
    mean_reversion_zscore:float|None=None
    short_horizon_return:float|None=None
    mean_reversion_score:float=0.0
    mean_reversion_confidence:float=0.0
    mean_reversion_side:str="HOLD"
    mean_reversion_horizon_minutes:int=0
    mean_reversion_available:bool=False
    mean_reversion_displacement_bps:float|None=None
    def to_dict(self): return asdict(self)

def _clip(x,a=-1,b=1): return max(a,min(b,x))

def _series(frame: pd.DataFrame, column: str) -> pd.Series:
    value = frame[column]
    if isinstance(value, pd.DataFrame):
        value = value.iloc[:, -1]
    return pd.to_numeric(value, errors="coerce").dropna()

def analyze_market(symbol, history, news_sentiment=0.0):
    if history is None or history.empty or len(history)<60:
        return None
    close=_series(history, "Close")
    if len(close)<60:
        return None
    price=float(close.iloc[-1])
    if not math.isfinite(price) or price <= 0:
        return None
    ret=close.pct_change().dropna()
    m5=float(close.iloc[-1]/close.iloc[-6]-1)
    m20=float(close.iloc[-1]/close.iloc[-21]-1)
    r=rsi(close)
    vol=float(ret.tail(20).std()*math.sqrt(252))
    sma10=float(close.tail(10).mean()); sma30=float(close.tail(30).mean())
    trend=(sma10/sma30)-1 if sma30 else 0
    vr=1.0
    if "Volume" in history:
        v=_series(history, "Volume")
        if len(v)>=20:
            av=float(v.tail(20).mean()); vr=float(v.iloc[-1]/av) if av else 1.0
    _,_,mh=macd(close)
    a=atr(history); atr_pct=a/price if price else 0
    bp=bollinger_position(close)
    reg=detect_regime(history)
    mean_reversion=assess_short_horizon_mean_reversion(
        symbol,
        history,
        rsi_value=r,
        atr_pct=atr_pct,
        volume_ratio=vr,
        regime=reg["name"],
    )

    raw=(0.30*_clip(m20/0.15)+0.15*_clip(m5/0.07)+0.20*_clip(trend/0.08)
         +0.10*_clip(news_sentiment)+0.08*_clip(mh/(price*0.01 if price else 1))
         +0.05*_clip((vr-1)/1.5))
    if r<30: raw+=0.10
    elif r>72: raw-=0.12
    if bp>0.95: raw-=0.05
    elif bp<0.05: raw+=0.05
    # Short-horizon crypto reversal is deliberately a bounded factor rather than
    # an order authorization. Existing quote, fee, liquidity, sizing and risk
    # gates remain authoritative downstream.
    if mean_reversion.available:
        raw+=0.12*_clip(mean_reversion.score)
    raw-=0.12*_clip(vol/1.2,0,1)
    if reg["name"]=="risk-off": raw-=0.06
    score=float(_clip(0.5+raw/2,0,1))
    action="BUY" if score>=SIGNAL_BUY_THRESHOLD else "SELL" if score<=SIGNAL_SELL_THRESHOLD else "HOLD"
    confidence=min(0.99,0.50+abs(score-0.5)*1.4)
    reason=(f"20d momentum {m20:+.1%}; RSI {r:.1f}; trend {trend:+.1%}; "
            f"volatility {vol:.1%}; regime {reg['name']}; news {news_sentiment:+.2f}.")
    if mean_reversion.available and mean_reversion.zscore is not None and mean_reversion.horizon_return is not None:
        reason += (
            f" Short-horizon reversion {mean_reversion.side}: "
            f"z {mean_reversion.zscore:+.2f}, "
            f"{mean_reversion.horizon_minutes}m return {mean_reversion.horizon_return:+.2%}, "
            f"factor {mean_reversion.score:+.2f}."
        )
    return OracleSignal(
        symbol,price,score,action,confidence,m5,m20,r,vol,trend,vr,news_sentiment,
        mh,atr_pct,bp,reg["name"],reason,
        mean_reversion_zscore=mean_reversion.zscore,
        short_horizon_return=mean_reversion.horizon_return,
        mean_reversion_score=mean_reversion.score,
        mean_reversion_confidence=mean_reversion.confidence,
        mean_reversion_side=mean_reversion.side,
        mean_reversion_horizon_minutes=mean_reversion.horizon_minutes,
        mean_reversion_available=mean_reversion.available,
        mean_reversion_displacement_bps=mean_reversion.displacement_bps,
    )
