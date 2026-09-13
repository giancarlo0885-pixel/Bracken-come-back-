import os

import numpy as np
import pandas as pd

from technical_indicators import rsi
from dip_rebound_strategy import assess_dip_rebound, backtest_dip_rebound
from entry_pattern_memory_runtime import expanded_feature_vector


def _history(values, interval="5m"):
    close = np.asarray(values, dtype=float)
    frame = pd.DataFrame({
        "Open": close,
        "High": close * 1.002,
        "Low": close * 0.998,
        "Close": close,
        "Volume": np.full(len(close), 1000.0),
    })
    frame.attrs["interval"] = interval
    frame.attrs["provider_route"] = {"interval": interval}
    return frame


def _enable_paper(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")


def test_wilder_rsi_handles_monotonic_and_flat_series():
    assert rsi(pd.Series(np.arange(1.0, 40.0))) == 100.0
    assert rsi(pd.Series(np.arange(40.0, 1.0, -1.0))) == 0.0
    assert rsi(pd.Series(np.full(40, 10.0))) == 50.0


def test_entry_pattern_memory_contains_dip_rebound_shape():
    signal = {
        "price": 100.0,
        "rsi_14": 38.0,
        "rsi_change": 7.0,
        "atr_pct": 0.018,
        "bollinger_position": 0.15,
        "macd_hist": 0.12,
        "mean_reversion_score": 0.55,
        "dip_rebound_score": 0.75,
        "dip_depth_pct": 0.045,
        "rebound_pct": 0.012,
        "drawdown_from_recent_high_pct": 0.034,
        "reclaim_strength": 0.006,
        "entry_pattern": "dip_rebound",
    }
    features = expanded_feature_vector(signal)
    for key in (
        "rsi_14", "rsi_change", "atr_pct", "bollinger_position",
        "macd_hist_norm", "mean_reversion_score", "dip_rebound_score",
        "dip_depth", "rebound", "recent_drawdown", "reclaim_strength",
        "dip_rebound_pattern",
    ):
        assert key in features
    assert features["dip_rebound_pattern"] == 1.0
    assert features["dip_depth"] > 0
    assert features["rebound"] > 0


def test_dip_rebound_requires_recovery_not_just_a_falling_price(monkeypatch):
    _enable_paper(monkeypatch)
    falling = _history(np.r_[np.full(55, 100.0), np.linspace(100.0, 94.0, 15)])
    result = assess_dip_rebound("BTC-USD", falling)
    assert result.available is True
    assert result.side != "BUY"


def test_dip_rebound_detects_confirmed_reclaim(monkeypatch):
    _enable_paper(monkeypatch)
    values = np.r_[
        np.full(55, 100.0),
        [99.5, 98.8, 98.0, 97.2, 96.5, 95.8, 95.2, 95.0],
        [95.1, 95.3, 95.6, 95.9, 96.2, 96.5, 96.8, 97.1, 97.4, 97.7],
    ]
    result = assess_dip_rebound("BTC-USD", _history(values))
    assert result.available is True
    assert result.dip_depth_pct >= 0.012
    assert result.rebound_pct >= 0.004
    assert result.rsi_change > 0
    assert result.side == "BUY"
    assert "ATR" in result.exit_rule


def test_paper_backtest_uses_tested_exit_rules_not_future_peak(monkeypatch):
    _enable_paper(monkeypatch)
    cycle = np.r_[
        np.full(12, 100.0),
        np.linspace(100.0, 95.0, 7),
        np.linspace(95.0, 99.0, 10),
        np.linspace(99.0, 101.5, 5),
        np.linspace(101.5, 99.5, 5),
    ]
    values = np.r_[np.full(50, 100.0), cycle, cycle * 1.01, cycle * 0.995]
    result = backtest_dip_rebound("BTC-USD", _history(values), fee_bps=10.0)
    assert set(result) >= {"trades", "net_return_pct", "wins", "losses", "exit_reasons"}
    assert result["trades"] >= 1
    assert sum(result["exit_reasons"].values()) == result["trades"]
    assert set(result["exit_reasons"]).issubset({
        "atr_profit_target", "atr_trailing_exit", "structural_stop", "extended_rebound_exit"
    })
