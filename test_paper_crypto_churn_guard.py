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
