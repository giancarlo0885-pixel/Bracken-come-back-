from __future__ import annotations
from config import MAX_POSITION_FRACTION, MIN_TRADE_VALUE

def position_size(equity: float, cash: float, confidence: float, volatility: float) -> float:
    confidence_factor=max(0.25,min(1.0,confidence))
    volatility_factor=max(0.25,min(1.0,0.35/(volatility+1e-9)))
    allocation=equity*MAX_POSITION_FRACTION*confidence_factor*volatility_factor
    allocation=min(cash,allocation)
    return allocation if allocation>=MIN_TRADE_VALUE else 0.0

def risk_grade(volatility: float, drawdown: float, concentration: float) -> str:
    score=volatility*0.5+abs(drawdown)*0.3+concentration*0.2
    if score<0.15: return "Low"
    if score<0.30: return "Moderate"
    if score<0.50: return "High"
    return "Extreme"
