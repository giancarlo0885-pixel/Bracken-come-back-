import pytest

import engine


def _clear_live(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")


def test_crypto_paper_learning_uses_relaxed_band(monkeypatch):
    _clear_live(monkeypatch)
    monkeypatch.delenv("PAPER_CRYPTO_BUY_THRESHOLD", raising=False)
    monkeypatch.delenv("PAPER_CRYPTO_SELL_THRESHOLD", raising=False)
    buy, sell = engine._signal_thresholds("BTC-USD")
    assert buy == 0.52
    assert sell == 0.48


def test_stock_keeps_normal_thresholds(monkeypatch):
    _clear_live(monkeypatch)
    buy, sell = engine._signal_thresholds("AAPL")
    assert buy == float(engine.SIGNAL_BUY_THRESHOLD)
    assert sell == float(engine.SIGNAL_SELL_THRESHOLD)


def test_broker_submission_disables_relaxation(monkeypatch):
    _clear_live(monkeypatch)
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "true")
    buy, sell = engine._signal_thresholds("BTC-USD")
    assert buy == float(engine.SIGNAL_BUY_THRESHOLD)
    assert sell == float(engine.SIGNAL_SELL_THRESHOLD)


def test_live_armed_disables_relaxation(monkeypatch):
    _clear_live(monkeypatch)
    monkeypatch.setenv("LIVE_TRADING_ARMED", "true")
    buy, sell = engine._signal_thresholds("ETH-USD")
    assert buy == float(engine.SIGNAL_BUY_THRESHOLD)
    assert sell == float(engine.SIGNAL_SELL_THRESHOLD)


def test_invalid_crossed_learning_band_is_repaired(monkeypatch):
    _clear_live(monkeypatch)
    monkeypatch.setenv("PAPER_CRYPTO_BUY_THRESHOLD", "0.50")
    monkeypatch.setenv("PAPER_CRYPTO_SELL_THRESHOLD", "0.50")
    buy, sell = engine._signal_thresholds("SOL-USD")
    assert sell < buy
    assert sell < 0.5 < buy


@pytest.mark.parametrize("missing", [
    "EXECUTION_MODE",
    "ENABLE_BROKER_SUBMISSION",
    "LIVE_TRADING_ARMED",
])
def test_missing_execution_control_keeps_default_thresholds(monkeypatch, missing):
    _clear_live(monkeypatch)
    monkeypatch.delenv(missing, raising=False)
    assert engine._signal_thresholds("BTC-USD") == (
        float(engine.SIGNAL_BUY_THRESHOLD),
        float(engine.SIGNAL_SELL_THRESHOLD),
    )


def test_all_execution_controls_missing_keep_default_thresholds(monkeypatch):
    _clear_live(monkeypatch)
    for name in ("EXECUTION_MODE", "ENABLE_BROKER_SUBMISSION", "LIVE_TRADING_ARMED"):
        monkeypatch.delenv(name, raising=False)
    assert engine._signal_thresholds("ETH-USD") == (
        float(engine.SIGNAL_BUY_THRESHOLD),
        float(engine.SIGNAL_SELL_THRESHOLD),
    )


@pytest.mark.parametrize(("name", "value"), [
    ("EXECUTION_MODE", "live"),
    ("ENABLE_BROKER_SUBMISSION", "true"),
    ("LIVE_TRADING_ARMED", "true"),
    ("ENABLE_BROKER_SUBMISSION", "0"),
])
def test_nonexplicit_paper_safety_control_keeps_default_thresholds(monkeypatch, name, value):
    _clear_live(monkeypatch)
    monkeypatch.setenv(name, value)
    assert engine._signal_thresholds("BTC-USD") == (
        float(engine.SIGNAL_BUY_THRESHOLD),
        float(engine.SIGNAL_SELL_THRESHOLD),
    )


def test_learning_opt_out_keeps_default_thresholds(monkeypatch):
    _clear_live(monkeypatch)
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "false")
    assert engine._signal_thresholds("BTC-USD") == (
        float(engine.SIGNAL_BUY_THRESHOLD),
        float(engine.SIGNAL_SELL_THRESHOLD),
    )
