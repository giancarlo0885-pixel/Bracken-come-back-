from __future__ import annotations

import numpy as np
import pandas as pd

import engine
from crypto_mean_reversion import assess_short_horizon_mean_reversion
from strategy_engine import evaluate_strategies


def _history(*, direction: str = "flat", interval: str = "5m") -> pd.DataFrame:
    rows = 90
    index = pd.date_range("2026-08-31T00:00:00Z", periods=rows, freq="5min")
    close = np.linspace(100.0, 101.0, rows)
    if direction == "down":
        close[-4:] = [101.0, 99.6, 98.4, 97.2]
    elif direction == "up":
        close[-4:] = [101.0, 102.4, 103.7, 105.0]
    frame = pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.001,
            "Low": close * 0.999,
            "Close": close,
            "Volume": np.concatenate([np.full(rows - 4, 1000.0), np.full(4, 2200.0)]),
        },
        index=index,
    )
    frame.attrs["provider_route"] = {
        "interval": interval,
        "requested_symbol": "BTC-USD",
        "provider_symbol": "BTC-USD",
        "quote_verified": True,
    }
    return frame


def test_downside_displacement_produces_buy_reversion_factor():
    assessment = assess_short_horizon_mean_reversion(
        "BTC-USD",
        _history(direction="down"),
        rsi_value=28.0,
        atr_pct=0.006,
        volume_ratio=2.0,
        regime="neutral",
    )
    assert assessment.available is True
    assert assessment.side == "BUY"
    assert assessment.score > 0
    assert assessment.zscore is not None and assessment.zscore < -1.25
    assert assessment.horizon_return is not None and assessment.horizon_return < 0
    assert assessment.horizon_minutes == 15


def test_upside_displacement_produces_sell_reversion_factor():
    assessment = assess_short_horizon_mean_reversion(
        "BTC-USD",
        _history(direction="up"),
        rsi_value=75.0,
        atr_pct=0.006,
        volume_ratio=2.0,
        regime="neutral",
    )
    assert assessment.available is True
    assert assessment.side == "SELL"
    assert assessment.score < 0
    assert assessment.zscore is not None and assessment.zscore > 1.25
    assert assessment.horizon_return is not None and assessment.horizon_return > 0


def test_risk_off_dampens_falling_knife_buy_factor():
    history = _history(direction="down")
    normal = assess_short_horizon_mean_reversion(
        "BTC-USD", history, rsi_value=28.0, atr_pct=0.006, volume_ratio=2.0, regime="neutral"
    )
    risk_off = assess_short_horizon_mean_reversion(
        "BTC-USD", history, rsi_value=28.0, atr_pct=0.006, volume_ratio=2.0, regime="risk-off"
    )
    assert 0 < risk_off.score < normal.score


def test_strategy_is_crypto_intraday_only():
    history = _history(direction="down")
    crypto = assess_short_horizon_mean_reversion("BTC-USD", history, rsi_value=28.0, atr_pct=0.006)
    stock = assess_short_horizon_mean_reversion("AAPL", history, rsi_value=28.0, atr_pct=0.006)
    hourly = _history(direction="down", interval="1h")
    hourly_crypto = assess_short_horizon_mean_reversion("BTC-USD", hourly, rsi_value=28.0, atr_pct=0.006)
    assert crypto.available is True
    assert stock.available is False
    assert hourly_crypto.available is False


def test_engine_emits_mean_reversion_evidence_for_fast_crypto_history():
    signal = engine.analyze_market("BTC-USD", _history(direction="down"), 0.0)
    assert signal is not None
    assert signal.mean_reversion_available is True
    assert signal.mean_reversion_side == "BUY"
    assert signal.mean_reversion_score > 0
    assert signal.mean_reversion_zscore is not None
    assert "Short-horizon reversion BUY" in signal.reason


def test_strategy_engine_consumes_real_mean_reversion_evidence():
    signal = engine.analyze_market("BTC-USD", _history(direction="down"), 0.0)
    assert signal is not None
    payload = signal.to_dict()
    payload["data_quality_score"] = 90.0
    result = evaluate_strategies(payload, ["mean_reversion"])[0]
    assert result.available is True
    assert result.score > 50.0
    assert result.confidence > 0.0
    assert result.evidence["side"] == "BUY"


def test_daily_or_deep_crypto_does_not_activate_short_horizon_factor():
    history = _history(direction="down", interval="1d")
    signal = engine.analyze_market("BTC-USD", history, 0.0)
    assert signal is not None
    assert signal.mean_reversion_available is False
    assert signal.mean_reversion_score == 0.0
