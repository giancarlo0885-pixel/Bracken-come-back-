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


def test_oracle_decision_persists_bounded_brain_influence():
    signal = {
        "symbol": "BRAIN", "score": 0.78, "confidence": 0.86, "action": "BUY",
        "momentum_5d": 0.03, "momentum_20d": 0.06,
        "trend_strength": 0.05, "volume_ratio": 1.5,
        "volatility_20d": 0.24, "atr_pct": 0.02,
        "news_sentiment": 0.4, "relative_strength": 0.08,
        "spread_pct": 0.0008, "estimated_slippage_pct": 0.0006,
        "event_risk_score": 10,
        "brain_outcome_adjustment": 2.25,
        "brain_intelligence_score": 76.0,
        "event_catalyst_score": 52.0,
        "research_direction": "positive",
        "research_directional_strength": 82.0,
        "research_directional_sources": 3,
        "ta_bullish_votes": 4,
        "ta_bearish_votes": 1,
        "schwager_setup_score": 0.6,
    }
    d = evaluate_opportunity(signal, use_market_memory=False)
    influence = d.brain_influence

    assert influence["outcome_memory_adjustment"] == 2.25
    assert influence["research_confluence_adjustment"] > 0
    assert influence["direct_radar_score_component"] > 2.25
    assert influence["brain_is_primary_external_catalyst"] is True
    assert influence["influences_decision"] is True
    assert influence["execution_authority"] == "NONE"
    assert influence["can_bypass_vetoes"] is False
    assert influence["live_money_impact"] == "NONE"
    assert any("Oracle Brain bounded influence" in step for step in d.explainability["decision_path"])
    assert "brain_influence" in d.to_dict()
