from quant_trade_standard import assess_trade


def test_strong_liquid_signal_is_approved():
    signal = {
        "score": 0.86, "confidence": 0.84, "momentum_5d": 0.04,
        "momentum_20d": 0.10, "trend_strength": 0.05,
        "volume_ratio": 1.5, "volatility_20d": 0.32, "atr_pct": 0.022,
        "news_sentiment": 0.45, "spread_pct": 0.0007,
        "estimated_slippage_pct": 0.0005, "event_risk_score": 15,
    }
    result = assess_trade(signal)
    assert result.approved
    assert result.trade_quality >= 68
    assert result.net_expected_value_pct > 0


def test_high_cost_signal_is_rejected():
    signal = {
        "score": 0.9, "confidence": 0.9, "momentum_20d": 0.12,
        "volume_ratio": 0.3, "volatility_20d": 1.1,
        "spread_pct": 0.02, "estimated_slippage_pct": 0.015,
        "event_risk_score": 85,
    }
    result = assess_trade(signal)
    assert not result.approved
    assert result.adverse_selection_score >= 70 or result.estimated_cost_pct > 0.02
