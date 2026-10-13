from dataclasses import asdict
from types import SimpleNamespace

import fomo_intelligence as fomo
import opportunity_radar as radar


def signal(**override):
    item = dict(
        symbol="BTC-USD",
        momentum_5d=0.03,
        momentum_20d=0.07,
        volume_ratio=1.6,
        trend_strength=0.02,
        rsi_14=64.0,
        bollinger_position=0.78,
    )
    item.update(override)
    return item


def test_fomo_emerging_rising_participation_without_overextension():
    result = fomo.assess_fomo(signal())
    assert result.phase == "emerging"
    assert result.available
    assert 0 <= result.intensity_score <= 100
    assert 0 <= result.exhaustion_score <= 100


def test_fomo_acceleration_is_distinct_from_exhaustion():
    result = fomo.assess_fomo(signal(
        momentum_5d=0.055, momentum_20d=0.11,
        volume_ratio=2.8, rsi_14=72, bollinger_position=0.92,
    ))
    assert result.phase == "acceleration"


def test_fomo_extended_and_fading_moves_are_not_early_entries():
    extended = fomo.assess_fomo(signal(
        momentum_5d=0.065, momentum_20d=0.15,
        volume_ratio=3.6, rsi_14=87, bollinger_position=1.18,
    ))
    fading = fomo.assess_fomo(signal(
        momentum_5d=-0.005, momentum_20d=0.09,
        volume_ratio=2.2, rsi_14=70, bollinger_position=0.78,
    ))
    assert extended.phase == "exhaustion"
    assert fading.phase == "exhaustion"


def test_fomo_does_not_fabricate_unavailable_or_nonfinite_market_data():
    for update in (
        {"volume_ratio": None},
        {"volume_ratio": 0},
        {"rsi_14": float("nan")},
        {"momentum_5d": float("inf")},
        {"rsi_14": 150},
    ):
        result = fomo.assess_fomo(signal(**update))
        assert result.phase == "unavailable"
        assert result.intensity_score is None
        assert not result.available
        assert result.missing_features


def test_fomo_no_opportunity_neutral_or_negative_momentum():
    result = fomo.assess_fomo(signal(
        momentum_5d=-0.04, momentum_20d=-0.12,
        volume_ratio=0.7, trend_strength=-0.05,
    ))
    assert result.phase == "quiet"


def test_radar_exposes_fomo_without_changing_any_existing_decision_inputs(monkeypatch):
    s = SimpleNamespace(**signal(
        price=100.0, volatility_20d=0.30, news_sentiment=0.4,
        macd_hist=0.8, atr_pct=0.025, regime="risk-on",
    ))
    before = asdict(radar.assess_opportunity_radar(s, market="crypto"))
    assert before["fomo_shadow"]["phase"] == "emerging"

    class FakeFomo:
        def to_dict(self):
            return {"phase": "exhaustion", "execution_impact": "NONE"}

    monkeypatch.setattr(radar, "assess_fomo", lambda _signal: FakeFomo())
    after = asdict(radar.assess_opportunity_radar(s, market="crypto"))
    assert after["fomo_shadow"]["phase"] == "exhaustion"
    before.pop("fomo_shadow")
    after.pop("fomo_shadow")
    assert before == after


def test_fomo_cannot_authorize_execution_or_promotion():
    for phase_signal in (
        signal(), signal(momentum_5d=0.06, momentum_20d=0.11, volume_ratio=2.3),
        signal(volume_ratio=None),
    ):
        result = fomo.assess_fomo(phase_signal).to_dict()
        assert result["research_only"] is True
        assert result["calibrated_probability"] is False
        assert result["execution_impact"] == "NONE"
        assert result["promotion_authorized"] is False
