from types import SimpleNamespace

import paper_fast_reversal_hysteresis as guard


def _signal(**overrides):
    data = dict(
        symbol="BNB-USD",
        action="SELL",
        score=0.47,
        confidence=0.55,
        momentum_5d=-0.001,
        momentum_20d=-0.002,
        trend_strength=-0.001,
        macd_hist=-0.1,
        rsi_14=55.0,
        mean_reversion_side="HOLD",
        mean_reversion_confidence=0.0,
        reason="test",
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def test_weak_recent_sell_is_suppressed(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")
    monkeypatch.setattr(guard, "_open_position_exists", lambda symbol: True)
    monkeypatch.setattr(guard, "_recent_entry_age_minutes", lambda symbol: 3.0)

    suppress, reason = guard._should_suppress(_signal())
    assert suppress is True
    assert "weak_recent_reversal" in reason


def test_strong_recent_reversal_is_not_suppressed(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")
    monkeypatch.setattr(guard, "_open_position_exists", lambda symbol: True)
    monkeypatch.setattr(guard, "_recent_entry_age_minutes", lambda symbol: 3.0)

    signal = _signal(
        score=0.38,
        confidence=0.72,
        momentum_5d=-0.02,
        momentum_20d=-0.03,
        trend_strength=-0.02,
        macd_hist=-0.2,
    )
    suppress, reason = guard._should_suppress(signal)
    assert suppress is False
    assert "strong_reversal" in reason


def test_sell_outside_hysteresis_is_not_suppressed(monkeypatch):
    monkeypatch.setattr(guard, "_open_position_exists", lambda symbol: True)
    monkeypatch.setattr(guard, "_recent_entry_age_minutes", lambda symbol: 20.0)

    suppress, reason = guard._should_suppress(_signal())
    assert suppress is False
    assert "outside_hysteresis" in reason


def test_non_sell_is_untouched(monkeypatch):
    suppress, reason = guard._should_suppress(_signal(action="HOLD"))
    assert suppress is False
    assert reason == "not_sell"
