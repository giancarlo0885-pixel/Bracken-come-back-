from market_memory import assess_market_memory, feature_vector, setup_similarity
from oracle_intelligence import evaluate_opportunity


def strong_signal():
    return {
        "symbol": "TEST", "score": 0.91, "confidence": 0.88,
        "momentum_5d": 0.05, "momentum_20d": 0.11,
        "trend_strength": 0.06, "volume_ratio": 1.7,
        "volatility_20d": 0.22, "atr_pct": 0.018,
        "news_sentiment": 0.6, "relative_strength": 0.12,
        "spread_pct": 0.0005, "estimated_slippage_pct": 0.0004,
        "event_risk_score": 10,
    }


def records(returns):
    f = feature_vector(strong_signal())
    return [
        {"symbol": f"OLD{i}", "market": "cash", "return_pct": ret,
         "payload": {"features": f}, "market_regime": "risk-on"}
        for i, ret in enumerate(returns)
    ]


def test_identical_setup_similarity_is_one():
    f = feature_vector(strong_signal())
    assert setup_similarity(f, f) == 1.0


def test_positive_history_supports_current_setup():
    memory = assess_market_memory(strong_signal(), records([0.04, 0.05, 0.02, 0.06, -0.01, 0.03, 0.07, 0.02]))
    assert memory.analog_count == 8
    assert memory.win_rate > 0.70
    assert memory.score_adjustment > 0
    assert not memory.veto


def test_repeated_negative_history_can_veto():
    memory = assess_market_memory(strong_signal(), records([-0.05, -0.04, -0.03, -0.06, -0.02, -0.07, -0.04, -0.03, -0.02, 0.01, -0.05, -0.04]))
    assert memory.win_rate < 0.30
    assert memory.score_adjustment < 0
    assert memory.veto


def test_oracle_exposes_market_memory_adjustment():
    decision = evaluate_opportunity(strong_signal(), historical_records=records([0.04, 0.03, 0.05, 0.02, 0.06, 0.03]))
    assert decision.memory["analog_count"] == 6
    assert decision.opportunity_score >= decision.base_opportunity_score
    assert "memory" in decision.to_dict()
