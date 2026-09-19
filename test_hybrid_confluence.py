from __future__ import annotations

from hybrid_confluence import assess_hybrid_confluence
from oracle_intelligence import evaluate_opportunity


def _strong_signal(**extra):
    signal = {
        "symbol": "TEST",
        "action": "BUY",
        "price": 100.0,
        "confidence": 0.90,
        "trend_score": 88.0,
        "momentum_score": 86.0,
        "volume_score": 84.0,
        "sentiment_score": 82.0,
        "atr_pct": 0.02,
        "spread_pct": 0.001,
        "estimated_slippage_pct": 0.0005,
        "event_risk_score": 10.0,
        "relative_strength": 82.0,
        "volatility_20d": 22.0,
        "distance_from_vwap_pct": 0.01,
    }
    signal.update(extra)
    return signal


def _strong_inputs():
    return {
        "quant": {
            "alpha_score": 88.0,
            "relative_value_score": 80.0,
            "risk_score": 82.0,
            "execution_score": 91.0,
            "adverse_selection_score": 12.0,
            "net_expected_value_pct": 0.025,
        },
        "memory": {
            "analog_count": 20,
            "analog_win_rate_pct": 68.0,
            "veto": False,
        },
        "global_intelligence": {
            "global_score": 78.0,
            "veto": False,
        },
        "radar": {
            "setup_score": 87.0,
            "veto": False,
        },
        "scenario": {
            "probability_of_profit": 74.0,
            "expected_return_pct": 4.2,
            "veto": False,
        },
    }


def test_super_hybrid_rewards_broad_high_quality_confluence():
    result = assess_hybrid_confluence(
        _strong_signal(),
        market="crypto",
        **_strong_inputs(),
    )
    assert result.score >= 70
    assert result.cross_signal_agreement >= 58
    assert result.execution_quality >= 65
    assert result.positive_boost_eligible is True
    assert result.score_adjustment > 0
    assert result.score_adjustment <= 4.0


def test_super_hybrid_never_positive_boosts_explicit_veto():
    inputs = _strong_inputs()
    inputs["memory"] = {**inputs["memory"], "veto": True}
    result = assess_hybrid_confluence(
        _strong_signal(),
        market="crypto",
        **inputs,
    )
    assert result.positive_boost_eligible is False
    assert result.score_adjustment <= 0


def test_super_hybrid_penalizes_conflict_and_poor_execution():
    inputs = _strong_inputs()
    inputs["quant"] = {
        **inputs["quant"],
        "alpha_score": 25.0,
        "relative_value_score": 20.0,
        "risk_score": 30.0,
        "execution_score": 28.0,
        "adverse_selection_score": 88.0,
        "net_expected_value_pct": -0.02,
    }
    inputs["scenario"] = {
        **inputs["scenario"],
        "probability_of_profit": 35.0,
        "expected_return_pct": -4.0,
    }
    result = assess_hybrid_confluence(
        _strong_signal(
            trend_score=90,
            momentum_score=18,
            volume_score=25,
            sentiment_score=15,
        ),
        market="crypto",
        **inputs,
    )
    assert result.positive_boost_eligible is False
    assert result.score_adjustment < 0
    assert result.execution_quality < 65


def test_oracle_exposes_hybrid_without_execution_authority():
    signal = _strong_signal(
        target_price=108.0,
        low_price=96.0,
        high_price=110.0,
    )
    decision = evaluate_opportunity(
        signal,
        market="crypto",
        historical_records=[],
        portfolio={"equity": 2000.0, "cash": 2000.0},
        positions=[],
    )
    payload = decision.to_dict()
    assert "hybrid" in payload
    assert payload["hybrid"]["execution_authority"] == "NONE"
    assert payload["hybrid"]["live_money_impact"] == "NONE"
    assert -5.0 <= payload["hybrid"]["applied_adjustment"] <= 4.0
