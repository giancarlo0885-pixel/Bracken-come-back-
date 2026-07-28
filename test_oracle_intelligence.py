from oracle_intelligence import evaluate_opportunity


def test_oracle_decision_exposes_actionable_metrics():
    signal = {
        "symbol": "TEST", "score": 0.92, "confidence": 0.9,
        "momentum_5d": 0.05, "momentum_20d": 0.10,
        "trend_strength": 0.07, "volume_ratio": 1.8,
        "volatility_20d": 0.22, "atr_pct": 0.018,
        "news_sentiment": 0.7, "relative_strength": 0.13,
        "spread_pct": 0.0005, "estimated_slippage_pct": 0.0004,
        "event_risk_score": 10,
    }
    d = evaluate_opportunity(signal)
    assert d.recommendation == "BUY"
    assert d.opportunity_score >= 68
    assert d.risk_reward_ratio > 1
    assert d.probability_of_profit > 50


def test_bad_execution_is_not_promoted():
    signal = {
        "symbol": "BAD", "score": 0.95, "confidence": 0.95,
        "momentum_20d": 0.12, "volume_ratio": 0.4,
        "spread_pct": 0.02, "estimated_slippage_pct": 0.02,
        "event_risk_score": 90,
    }
    d = evaluate_opportunity(signal)
    assert d.recommendation != "BUY"
