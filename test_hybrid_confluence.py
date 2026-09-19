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
    assert result.source_diversity >= 70
    assert result.cost_adjusted_conviction >= 50
    assert result.adversarial_margin > 0
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



def test_super_hybrid_withholds_boost_when_costs_consume_the_edge():
    inputs = _strong_inputs()
    inputs["quant"] = {
        **inputs["quant"],
        "net_expected_value_pct": 0.004,
        "estimated_cost_pct": 0.012,
    }
    result = assess_hybrid_confluence(
        _strong_signal(),
        market="crypto",
        **inputs,
    )
    assert result.cost_adjusted_conviction < 50
    assert result.positive_boost_eligible is False
    assert result.score_adjustment <= 0


def test_super_hybrid_regime_memory_distinguishes_bad_matching_history():
    inputs = _strong_inputs()
    inputs["memory"] = {
        **inputs["memory"],
        "analogs": [
            {"regime": "risk_on", "return_pct": -0.04},
            {"regime": "risk_on", "return_pct": -0.03},
            {"regime": "risk_on", "return_pct": -0.02},
            {"regime": "neutral", "return_pct": 0.05},
        ],
    }
    result = assess_hybrid_confluence(
        _strong_signal(regime="risk_on"),
        market="crypto",
        **inputs,
    )
    assert result.regime_memory_quality < 50


def test_super_hybrid_tracks_persistence_and_forecast_calibration():
    result = assess_hybrid_confluence(
        _strong_signal(
            regime="risk_on",
            consecutive_confirmations=4,
            forecast_validation_samples=60,
            directional_accuracy=0.66,
            calibration_error=0.08,
        ),
        market="crypto",
        **_strong_inputs(),
    )
    assert result.edge_persistence >= 85
    assert result.calibration_confidence > 60
    assert result.source_diversity == 100


def test_super_hybrid_reads_real_market_memory_win_rate_field():
    inputs = _strong_inputs()
    inputs["memory"] = {
        "analog_count": 20,
        "win_rate": 0.70,
        "analogs": [],
        "veto": False,
    }
    result = assess_hybrid_confluence(
        _strong_signal(regime="risk_on"),
        market="crypto",
        **inputs,
    )
    assert result.historical_edge_quality > 50
