from datetime import datetime, timedelta, timezone

import paper_crypto_churn_guard as guard


def _signal(symbol="BNB-USD", action="SELL", price=100.0):
    return {"symbol": symbol, "action": action, "price": price}


def test_generic_sell_blocked_during_minimum_hold(monkeypatch):
    monkeypatch.setenv("PAPER_CRYPTO_MIN_SIGNAL_HOLD_MINUTES", "5")
    monkeypatch.setenv("PAPER_CRYPTO_EMERGENCY_EXIT_LOSS_PCT", "6")
    monkeypatch.setattr(
        guard,
        "_position_and_last_buy",
        lambda symbol: ({"average_price": 100.0}, datetime.now(timezone.utc) - timedelta(seconds=30)),
    )
    allowed, reason = guard._allow_generic_sell(_signal(price=99.8), {"BNB-USD": {"price": 99.8}})
    assert allowed is False
    assert reason.startswith("minimum_hold:")


def test_material_loss_bypasses_minimum_hold(monkeypatch):
    monkeypatch.setenv("PAPER_CRYPTO_MIN_SIGNAL_HOLD_MINUTES", "5")
    monkeypatch.setenv("PAPER_CRYPTO_EMERGENCY_EXIT_LOSS_PCT", "6")
    monkeypatch.setattr(
        guard,
        "_position_and_last_buy",
        lambda symbol: ({"average_price": 100.0}, datetime.now(timezone.utc) - timedelta(seconds=30)),
    )
    allowed, reason = guard._allow_generic_sell(_signal(price=93.0), {"BNB-USD": {"price": 93.0}})
    assert allowed is True
    assert reason.startswith("emergency_loss_override:")


def test_generic_sell_requires_confirmation_after_hold(monkeypatch):
    monkeypatch.setenv("PAPER_CRYPTO_MIN_SIGNAL_HOLD_MINUTES", "5")
    monkeypatch.setenv("PAPER_CRYPTO_SELL_CONFIRMATIONS", "2")
    monkeypatch.setenv("PAPER_CRYPTO_SELL_CONFIRMATION_WINDOW_SECONDS", "120")
    monkeypatch.setattr(
        guard,
        "_position_and_last_buy",
        lambda symbol: ({"average_price": 100.0}, datetime.now(timezone.utc) - timedelta(minutes=10)),
    )
    guard._SELL_CONFIRMATIONS.clear()
    first, first_reason = guard._allow_generic_sell(_signal(price=99.5), {"BNB-USD": {"price": 99.5}})
    second, second_reason = guard._allow_generic_sell(_signal(price=99.4), {"BNB-USD": {"price": 99.4}})
    assert first is False
    assert first_reason == "sell_confirmation:1/2"
    assert second is True
    assert second_reason == "sell_confirmed:2/2"


def test_exit_and_close_are_not_delayed():
    assert guard._allow_generic_sell(_signal(action="EXIT"), {}) == (True, "not_generic_sell")
    assert guard._allow_generic_sell(_signal(action="CLOSE"), {}) == (True, "not_generic_sell")


def _patch_trade_history(monkeypatch, *, sell_age_minutes, sell_price=101.0, buy_price=100.0, reentered=False):
    now = datetime.now(timezone.utc)
    sell_time = now - timedelta(minutes=sell_age_minutes)
    buy_time = sell_time + timedelta(seconds=30) if reentered else sell_time - timedelta(minutes=15)

    def fake_last_trade(symbol, side):
        if side == "SELL":
            return {"created_at": sell_time, "price": sell_price}
        return {"created_at": buy_time, "price": buy_price}

    monkeypatch.setattr(guard, "_last_trade", fake_last_trade)


def test_buy_reentry_blocked_after_recent_sell(monkeypatch):
    monkeypatch.setenv("PAPER_CRYPTO_REENTRY_COOLDOWN_MINUTES", "10")
    _patch_trade_history(monkeypatch, sell_age_minutes=2, sell_price=101.0, buy_price=100.0)
    allowed, reason = guard._allow_generic_buy(_signal(action="BUY"))
    assert allowed is False
    assert reason.startswith("reentry_cooldown:")


def test_buy_reentry_allowed_after_cooldown(monkeypatch):
    monkeypatch.setenv("PAPER_CRYPTO_REENTRY_COOLDOWN_MINUTES", "10")
    _patch_trade_history(monkeypatch, sell_age_minutes=20, sell_price=101.0, buy_price=100.0)
    assert guard._allow_generic_buy(_signal(action="BUY")) == (True, "reentry_cooldown_elapsed")


def test_losing_exit_gets_longer_reentry_cooldown(monkeypatch):
    monkeypatch.setenv("PAPER_CRYPTO_REENTRY_COOLDOWN_MINUTES", "10")
    monkeypatch.setenv("PAPER_CRYPTO_LOSS_REENTRY_COOLDOWN_MULTIPLIER", "2")
    _patch_trade_history(monkeypatch, sell_age_minutes=15, sell_price=99.0, buy_price=100.0)
    allowed, reason = guard._allow_generic_buy(_signal(action="BUY"))
    assert allowed is False
    assert reason.startswith("loss_reentry_cooldown:")
    assert reason.endswith("/20.00m")


def test_losing_exit_reentry_allowed_after_extended_cooldown(monkeypatch):
    monkeypatch.setenv("PAPER_CRYPTO_REENTRY_COOLDOWN_MINUTES", "10")
    monkeypatch.setenv("PAPER_CRYPTO_LOSS_REENTRY_COOLDOWN_MULTIPLIER", "2")
    _patch_trade_history(monkeypatch, sell_age_minutes=25, sell_price=99.0, buy_price=100.0)
    assert guard._allow_generic_buy(_signal(action="BUY")) == (True, "loss_reentry_cooldown_elapsed")


def test_buy_not_blocked_when_already_reentered(monkeypatch):
    monkeypatch.setenv("PAPER_CRYPTO_REENTRY_COOLDOWN_MINUTES", "10")
    _patch_trade_history(monkeypatch, sell_age_minutes=2, sell_price=99.0, buy_price=100.0, reentered=True)
    assert guard._allow_generic_buy(_signal(action="BUY")) == (True, "already_reentered")


def test_unbounded_learning_keeps_downstream_churn_guard(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("PAPER_UNBOUNDED_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")
    monkeypatch.setenv("PAPER_CRYPTO_MIN_SIGNAL_HOLD_MINUTES", "5")
    monkeypatch.setattr(
        guard,
        "_position_and_last_buy",
        lambda symbol: ({"average_price": 100.0}, datetime.now(timezone.utc) - timedelta(seconds=30)),
    )

    calls = []

    class Worker:
        def process_signals(self, market, signals, prices=None, *args, **kwargs):
            calls.append((market, signals, prices))
            return signals

    previous = guard._INSTALLED
    guard._INSTALLED = False
    try:
        worker = Worker()
        assert guard.install_paper_crypto_churn_guard(worker) is True
        result = worker.process_signals("crypto", [_signal(price=99.8)], {"BNB-USD": {"price": 99.8}})
        assert result == []
        assert calls == []
    finally:
        guard._INSTALLED = previous
