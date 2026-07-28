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
