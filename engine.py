from __future__ import annotations
from dataclasses import dataclass, asdict
import math, os, re, numpy as np, pandas as pd
from config import SIGNAL_BUY_THRESHOLD, SIGNAL_SELL_THRESHOLD
from crypto_mean_reversion import assess_short_horizon_mean_reversion
from dip_rebound_strategy import assess_dip_rebound
from technical_indicators import rsi, macd, atr, bollinger_position
from schwager_technical_framework import assess_schwager_technical_structure
from technical_book_ensemble import assess_technical_book_ensemble
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
    entry_pattern:str=""
    dip_rebound_available:bool=False
    dip_rebound_side:str="HOLD"
    dip_rebound_score:float=0.0
    dip_rebound_confidence:float=0.0
    dip_depth_pct:float|None=None
    rebound_pct:float|None=None
    drawdown_from_recent_high_pct:float|None=None
    rsi_change:float|None=None
    reclaim_strength:float|None=None
    dip_rebound_exit_rule:str=""
    schwager_ta_available:bool=False
    schwager_trend_state:str="unknown"
    schwager_trend_score:float=0.0
    schwager_support:float|None=None
    schwager_resistance:float|None=None
    schwager_support_distance_pct:float|None=None
    schwager_resistance_distance_pct:float|None=None
    schwager_breakout_state:str="none"
    schwager_breakout_score:float=0.0
    schwager_failed_breakout:bool=False
    schwager_oscillator_state:str="neutral"
    schwager_oscillator_score:float=0.0
    schwager_setup_score:float=0.0
    schwager_pattern_tag:str=""
    schwager_suggested_stop:float|None=None
    schwager_objective_1:float|None=None
    schwager_reward_risk_ratio:float|None=None
    ta_ensemble_available:bool=False
    ta_pring_score:float=0.0
    ta_murphy_score:float=0.0
    ta_oneil_score:float=0.0
    ta_nison_score:float=0.0
    ta_bulkowski_score:float=0.0
    ta_shannon_score:float=0.0
    ta_consensus_score:float=0.0
    ta_agreement_count:int=0
    ta_conflict_score:float=0.0
    ta_bullish_votes:int=0
    ta_bearish_votes:int=0
    ta_neutral_votes:int=0
    ta_metric_version:str=""
    def to_dict(self): return asdict(self)

def _clip(x,a=-1,b=1): return max(a,min(b,x))

def _truthy(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default) or default).strip().lower() == "true"

def _crypto_symbol(symbol: str) -> bool:
    text = str(symbol or "").strip().upper()
    return text.endswith("-USD") or text.endswith("USD")

def _signal_thresholds(symbol: str) -> tuple[float, float]:
    paper_learning = (
        str(os.getenv("EXECUTION_MODE", "paper") or "paper").strip().lower() == "paper"
        and _truthy("PAPER_AUTONOMOUS_LEARNING")
        and not _truthy("ENABLE_BROKER_SUBMISSION")
        and not _truthy("LIVE_TRADING_ARMED")
        and _crypto_symbol(symbol)
    )
    if not paper_learning:
        return float(SIGNAL_BUY_THRESHOLD), float(SIGNAL_SELL_THRESHOLD)
    buy = float(os.getenv("PAPER_CRYPTO_BUY_THRESHOLD", "0.52"))
    sell = float(os.getenv("PAPER_CRYPTO_SELL_THRESHOLD", "0.48"))
    buy = max(0.50, min(0.99, buy))
    sell = max(0.01, min(0.50, sell))
    if sell >= buy:
        midpoint = (sell + buy) / 2.0
        sell = min(0.499, midpoint - 0.001)
        buy = max(0.501, midpoint + 0.001)
    return buy, sell

def _series(frame: pd.DataFrame, column: str) -> pd.Series:
    value = frame[column]
    if isinstance(value, pd.DataFrame):
        value = value.iloc[:, -1]
    return pd.to_numeric(value, errors="coerce").dropna()

def _history_interval(history: pd.DataFrame) -> str:
    attrs = dict(getattr(history, "attrs", {}) or {})
    route = dict(attrs.get("provider_route", {}) or {})
    return str(route.get("interval") or route.get("source_interval") or attrs.get("interval") or attrs.get("source_interval") or "1d").strip().lower()

def _bars_per_year(symbol: str, history: pd.DataFrame) -> float:
    interval = _history_interval(history)
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([mhd])\s*", interval)
    crypto = _crypto_symbol(symbol)
    if not match:
        return 365.0 if crypto else 252.0
    amount = max(float(match.group(1)), 1e-9)
    unit = match.group(2)
    if crypto:
        if unit == "m": return max(1.0, 365.0 * 24.0 * 60.0 / amount)
        if unit == "h": return max(1.0, 365.0 * 24.0 / amount)
        return max(1.0, 365.0 / amount)
    if unit == "m": return max(1.0, 252.0 * 390.0 / amount)
    if unit == "h": return max(1.0, 252.0 * 6.5 / amount)
    return max(1.0, 252.0 / amount)

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
    vol=float(ret.tail(20).std()*math.sqrt(_bars_per_year(symbol, history)))
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
    mean_reversion=assess_short_horizon_mean_reversion(symbol,history,rsi_value=r,atr_pct=atr_pct,volume_ratio=vr,regime=reg["name"])
    dip_rebound=assess_dip_rebound(symbol, history)
    schwager=assess_schwager_technical_structure(history)
    ta_ensemble=assess_technical_book_ensemble(history, schwager_score=schwager.setup_score)

    raw=(0.30*_clip(m20/0.15)+0.15*_clip(m5/0.07)+0.20*_clip(trend/0.08)
         +0.10*_clip(news_sentiment)+0.08*_clip(mh/(price*0.01 if price else 1))
         +0.05*_clip((vr-1)/1.5))
    if r<30: raw+=0.10
    elif r>72: raw-=0.12
    if bp>0.95: raw-=0.05
    elif bp<0.05: raw+=0.05
    if mean_reversion.available:
        raw+=0.12*_clip(mean_reversion.score)
    if dip_rebound.available:
        raw+=0.16*_clip(dip_rebound.score)
    raw-=0.12*_clip(vol/1.2,0,1)
    if reg["name"]=="risk-off": raw-=0.06
    score=float(_clip(0.5+raw/2,0,1))
    buy_threshold, sell_threshold = _signal_thresholds(symbol)
    action="BUY" if score>=buy_threshold else "SELL" if score<=sell_threshold else "HOLD"

    entry_pattern=""
    if dip_rebound.available and dip_rebound.side=="BUY" and dip_rebound.confidence>=0.58 and score>=max(0.48,buy_threshold-0.06):
        action="BUY"
        entry_pattern="dip_rebound"
    elif dip_rebound.available and dip_rebound.side=="SELL" and score<=min(0.56,sell_threshold+0.08):
        action="SELL"
        entry_pattern="dip_rebound_exit"
    elif schwager.available and schwager.pattern_tag and abs(schwager.setup_score) >= 0.55:
        entry_pattern=schwager.pattern_tag

    confidence=min(0.99,0.50+abs(score-0.5)*1.4)
    if entry_pattern=="dip_rebound" and dip_rebound.confidence>confidence:
        confidence=min(0.99,dip_rebound.confidence)
    reason=(f"20d momentum {m20:+.1%}; RSI {r:.1f}; trend {trend:+.1%}; volatility {vol:.1%}; regime {reg['name']}; news {news_sentiment:+.2f}.")
    if mean_reversion.available and mean_reversion.zscore is not None and mean_reversion.horizon_return is not None:
        reason += (f" Short-horizon reversion {mean_reversion.side}: z {mean_reversion.zscore:+.2f}, {mean_reversion.horizon_minutes}m return {mean_reversion.horizon_return:+.2%}, factor {mean_reversion.score:+.2f}.")
    if dip_rebound.available:
        reason += f" Dip/rebound {dip_rebound.reason} Exit rule: {dip_rebound.exit_rule}."
    if schwager.available:
        reason += f" {schwager.reason}"
    if ta_ensemble.available:
        reason += f" {ta_ensemble.reason}"

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
        entry_pattern=entry_pattern,
        dip_rebound_available=dip_rebound.available,
        dip_rebound_side=dip_rebound.side,
        dip_rebound_score=dip_rebound.score,
        dip_rebound_confidence=dip_rebound.confidence,
        dip_depth_pct=dip_rebound.dip_depth_pct,
        rebound_pct=dip_rebound.rebound_pct,
        drawdown_from_recent_high_pct=dip_rebound.drawdown_from_recent_high_pct,
        rsi_change=dip_rebound.rsi_change,
        reclaim_strength=dip_rebound.reclaim_strength,
        dip_rebound_exit_rule=dip_rebound.exit_rule,
        schwager_ta_available=schwager.available,
        schwager_trend_state=schwager.trend_state,
        schwager_trend_score=schwager.trend_score,
        schwager_support=schwager.support,
        schwager_resistance=schwager.resistance,
        schwager_support_distance_pct=schwager.support_distance_pct,
        schwager_resistance_distance_pct=schwager.resistance_distance_pct,
        schwager_breakout_state=schwager.breakout_state,
        schwager_breakout_score=schwager.breakout_score,
        schwager_failed_breakout=schwager.failed_breakout,
        schwager_oscillator_state=schwager.oscillator_state,
        schwager_oscillator_score=schwager.oscillator_score,
        schwager_setup_score=schwager.setup_score,
        schwager_pattern_tag=schwager.pattern_tag,
        schwager_suggested_stop=schwager.suggested_stop,
        schwager_objective_1=schwager.objective_1,
        schwager_reward_risk_ratio=schwager.reward_risk_ratio,
        ta_ensemble_available=ta_ensemble.available,
        ta_pring_score=ta_ensemble.pring_cycle_momentum_score,
        ta_murphy_score=ta_ensemble.murphy_confirmation_score,
        ta_oneil_score=ta_ensemble.oneil_breakout_quality_score,
        ta_nison_score=ta_ensemble.nison_candlestick_context_score,
        ta_bulkowski_score=ta_ensemble.bulkowski_pattern_quality_score,
        ta_shannon_score=ta_ensemble.shannon_multihorizon_alignment_score,
        ta_consensus_score=ta_ensemble.consensus_score,
        ta_agreement_count=ta_ensemble.agreement_count,
        ta_conflict_score=ta_ensemble.conflict_score,
        ta_bullish_votes=ta_ensemble.bullish_votes,
        ta_bearish_votes=ta_ensemble.bearish_votes,
        ta_neutral_votes=ta_ensemble.neutral_votes,
        ta_metric_version=ta_ensemble.metric_version,
    )
