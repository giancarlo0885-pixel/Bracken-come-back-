from types import SimpleNamespace
from opportunity_radar import assess_opportunity_radar


def signal(**overrides):
    base = dict(
        symbol="TEST", price=100.0, momentum_5d=0.06, momentum_20d=0.15,
        rsi_14=64.0, volatility_20d=0.30, trend_strength=0.09,
        volume_ratio=1.8, news_sentiment=0.4, macd_hist=0.8,
        atr_pct=0.025, bollinger_position=0.82, regime="risk-on",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_breakout_is_classified_and_approved():
    result = assess_opportunity_radar(signal())
    assert result.primary_setup in {"MOMENTUM BREAKOUT", "TREND CONTINUATION", "SECTOR LEADERSHIP"}
    assert result.setup_score >= 65
    assert result.approved
    assert not result.veto


def test_extreme_crowding_can_veto():
    result = assess_opportunity_radar(signal(rsi_14=95, bollinger_position=1.6, volume_ratio=5.5, volatility_20d=1.0))
    assert result.crowding_risk >= 80
    assert result.veto


def test_mean_reversion_detected():
    result = assess_opportunity_radar(signal(momentum_5d=-0.05, momentum_20d=0.02, trend_strength=0.01, rsi_14=24, bollinger_position=-0.05, volume_ratio=1.2))
    assert result.primary_setup == "MEAN REVERSION"


def test_sourced_research_and_pattern_alignment_raise_confluence():
    result = assess_opportunity_radar(signal(
        action="BUY",
        research_direction="positive",
        research_directional_strength=85.0,
        research_directional_sources=3,
        ta_bullish_votes=5,
        ta_bearish_votes=1,
        schwager_setup_score=0.70,
        dip_rebound_side="BUY",
    ))
    assert result.research_direction == "positive"
    assert result.pattern_direction == "positive"
    assert result.pattern_strength > 0
    assert result.confluence_score > 0
    assert any("research agrees" in reason for reason in result.reasons)


def test_research_pattern_conflict_is_penalized_not_converted_into_trade():
    result = assess_opportunity_radar(signal(
        action="BUY",
        research_direction="negative",
        research_directional_strength=90.0,
        research_directional_sources=3,
        ta_bullish_votes=5,
        ta_bearish_votes=1,
        schwager_setup_score=0.75,
        dip_rebound_side="BUY",
    ))
    assert result.research_direction == "negative"
    assert result.pattern_direction == "positive"
    assert result.confluence_score < 0
    assert any("conflicts" in warning for warning in result.warnings)


def test_research_without_pattern_confirmation_does_not_add_positive_confluence():
    result = assess_opportunity_radar(signal(
        action="BUY",
        research_direction="positive",
        research_directional_strength=95.0,
        research_directional_sources=4,
        ta_bullish_votes=1,
        ta_bearish_votes=1,
        schwager_setup_score=0.0,
        dip_rebound_side="",
    ))
    assert result.pattern_direction == "neutral"
    assert result.confluence_score == 0.0
    assert any("technical pattern confirmation is weak" in warning for warning in result.warnings)
