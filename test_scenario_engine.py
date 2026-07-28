from scenario_engine import assess_scenarios
from quant_trade_standard import assess_trade


def strong_signal():
    return {"symbol":"ALPHA","price":100,"score":95,"confidence":0.94,"momentum_5d":0.06,"momentum_20d":0.18,"trend_strength":0.08,"volume_ratio":2.2,"volatility_20d":0.22,"atr_pct":0.014,"news_sentiment":0.7,"relative_strength":0.18,"spread_pct":0.0004,"estimated_slippage_pct":0.0003,"event_risk_score":8,"regime":"bull"}


def weak_signal():
    return {"symbol":"RISK","price":100,"score":70,"confidence":0.58,"momentum_5d":-0.02,"momentum_20d":-0.08,"trend_strength":-0.05,"volume_ratio":0.5,"volatility_20d":1.2,"atr_pct":0.08,"news_sentiment":-0.4,"relative_strength":-0.15,"spread_pct":0.008,"estimated_slippage_pct":0.007,"event_risk_score":90,"regime":"crisis"}


def test_strong_scenario_is_favorable_and_sized():
    sig=strong_signal(); quant=assess_trade(sig)
    result=assess_scenarios(sig,quant=quant,market="cash",paths=800)
    assert result.probability_of_profit >= 52
    assert result.expected_return_pct > 0
    assert result.position_multiplier > 0.2
    assert result.verdict in {"FAVORABLE","BALANCED"}


def test_risky_scenario_is_vetoed():
    sig=weak_signal(); quant=assess_trade(sig)
    result=assess_scenarios(sig,quant=quant,market="cash",paths=800)
    assert result.veto
    assert not result.approved
    assert result.expected_shortfall_95_pct < 0


def test_scenario_is_deterministic_for_same_inputs():
    sig=strong_signal(); quant=assess_trade(sig)
    first=assess_scenarios(sig,quant=quant,paths=600)
    second=assess_scenarios(sig,quant=quant,paths=600)
    assert first.to_dict() == second.to_dict()
