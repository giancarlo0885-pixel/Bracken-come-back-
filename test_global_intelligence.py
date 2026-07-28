from global_intelligence import assess_global_intelligence
from oracle_intelligence import evaluate_opportunity


def strong_signal():
    return {"symbol":"NVDA","action":"BUY","score":92,"confidence":0.92,"atr_pct":0.018,"volume_ratio":1.8,"spread_pct":0.001,"slippage_pct":0.001,"sector":"TECHNOLOGY","sector_relative_strength":1.4}


def test_risk_on_cross_market_confirmation():
    a=assess_global_intelligence(strong_signal(), {"vix":15,"spy_change_pct":1.2,"bitcoin_change_pct":2.0,"market_breadth":55,"liquidity_score":85,"treasury_10y_change_bps":-4})
    assert a.global_score >= 65
    assert a.score_adjustment > 0
    assert not a.veto


def test_extreme_global_conflict_can_veto():
    s=strong_signal(); s["sector_relative_strength"]=-3.0
    a=assess_global_intelligence(s, {"vix":42,"spy_change_pct":-3.2,"market_breadth":-80,"credit_spread_change_bps":30,"liquidity_score":25})
    assert a.veto
    assert a.position_multiplier <= .35


def test_oracle_exposes_global_intelligence():
    d=evaluate_opportunity(strong_signal(), global_context={"vix":16,"spy_change_pct":1,"market_breadth":40,"liquidity_score":80})
    assert "global_score" in d.global_intelligence
    assert "global" in d.reason.lower()
