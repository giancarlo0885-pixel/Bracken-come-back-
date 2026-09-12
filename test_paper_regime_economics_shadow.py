from __future__ import annotations

import paper_regime_economics_shadow as regime


def _paper(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")


def test_shadow_is_fail_closed_for_live(monkeypatch):
    _paper(monkeypatch)
    assert regime.active() is True
    monkeypatch.setenv("LIVE_TRADING_ARMED", "true")
    assert regime.active() is False


def test_existing_market_memory_regime_wins():
    assert regime.classify_regime(
        feature_snapshot={"trend_strength": -0.2, "volatility_20d": 0.9},
        memory_regime="RISK OFF",
    ) == "risk_off"


def test_regime_uses_only_entry_features():
    assert regime.classify_regime(
        feature_snapshot={"trend_strength": 0.10, "momentum_20d": 0.08, "volatility_20d": 0.75}
    ) == "trend_up__high_vol"
    assert regime.classify_regime(
        feature_snapshot={"trend_strength": -0.12, "momentum_20d": -0.06, "volatility_20d": 0.20}
    ) == "trend_down__low_vol"
    assert regime.classify_regime(
        feature_snapshot={"trend_strength": 0.01, "momentum_20d": -0.02}
    ) == "range__vol_unknown"


def test_unknown_features_do_not_invent_volatility():
    assert regime.classify_regime(feature_snapshot={}) == "range__vol_unknown"
