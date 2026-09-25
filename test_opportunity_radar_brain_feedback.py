from opportunity_radar import assess_opportunity_radar


def _signal(adjustment: float = 0.0):
    return {
        "price": 100.0,
        "momentum_5d": 0.02,
        "momentum_20d": 0.03,
        "trend_strength": 0.10,
        "rsi_14": 55.0,
        "volume_ratio": 1.10,
        "news_sentiment": 0.20,
        "macd_hist": 0.10,
        "atr_pct": 0.02,
        "bollinger_position": 0.60,
        "volatility_20d": 0.25,
        "regime": "bull",
        "brain_outcome_adjustment": adjustment,
    }


def test_brain_outcome_feedback_changes_only_bounded_radar_score():
    baseline = assess_opportunity_radar(_signal(0.0), market="crypto")
    positive = assess_opportunity_radar(_signal(3.0), market="crypto")
    negative = assess_opportunity_radar(_signal(-4.0), market="crypto")

    assert positive.radar_adjustment > baseline.radar_adjustment
    assert negative.radar_adjustment < baseline.radar_adjustment
    assert positive.radar_adjustment <= 6.0
    assert negative.radar_adjustment >= -6.0
    assert positive.approved == baseline.approved == negative.approved
    assert positive.veto == baseline.veto == negative.veto
    assert any("Brain outcomes" in reason for reason in positive.reasons)
    assert any("Brain outcomes" in warning for warning in negative.warnings)


def test_brain_outcome_feedback_is_clamped():
    positive = assess_opportunity_radar(_signal(999.0), market="crypto")
    negative = assess_opportunity_radar(_signal(-999.0), market="crypto")

    assert positive.radar_adjustment <= 6.0
    assert negative.radar_adjustment >= -6.0
