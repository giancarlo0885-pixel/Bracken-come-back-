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
