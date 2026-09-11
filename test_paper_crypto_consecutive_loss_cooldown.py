from datetime import datetime, timedelta, timezone

import paper_crypto_churn_guard as guard


def _patch_history(monkeypatch, *, sell_age_minutes=40, sell_price=99.0, buy_price=100.0, streak=1):
    now = datetime.now(timezone.utc)
    sell_time = now - timedelta(minutes=sell_age_minutes)
    buy_time = sell_time - timedelta(minutes=15)

    def fake_last_trade(symbol, side):
        if side == "SELL":
            return {"created_at": sell_time, "price": sell_price}
        return {"created_at": buy_time, "price": buy_price}

    monkeypatch.setattr(guard, "_last_trade", fake_last_trade)
    monkeypatch.setattr(guard, "_recent_realized_loss_streak", lambda symbol: streak)


def test_first_loss_keeps_thirty_minute_default(monkeypatch):
    monkeypatch.setenv("PAPER_CRYPTO_REENTRY_COOLDOWN_MINUTES", "10")
    monkeypatch.setenv("PAPER_CRYPTO_LOSS_REENTRY_COOLDOWN_MULTIPLIER", "3")
    monkeypatch.setenv("PAPER_CRYPTO_CONSECUTIVE_LOSS_COOLDOWN_STEP", "0.5")
    _patch_history(monkeypatch, sell_age_minutes=25, streak=1)

    allowed, reason = guard._allow_generic_buy({"symbol": "SOL-USD", "action": "BUY"})

    assert allowed is False
    assert "streak=1" in reason
    assert reason.endswith("/30.00m")


def test_second_consecutive_loss_extends_to_forty_five_minutes(monkeypatch):
    monkeypatch.setenv("PAPER_CRYPTO_REENTRY_COOLDOWN_MINUTES", "10")
    monkeypatch.setenv("PAPER_CRYPTO_LOSS_REENTRY_COOLDOWN_MULTIPLIER", "3")
    monkeypatch.setenv("PAPER_CRYPTO_CONSECUTIVE_LOSS_COOLDOWN_STEP", "0.5")
    _patch_history(monkeypatch, sell_age_minutes=40, streak=2)

    allowed, reason = guard._allow_generic_buy({"symbol": "SOL-USD", "action": "BUY"})

    assert allowed is False
    assert "streak=2" in reason
    assert reason.endswith("/45.00m")


def test_third_consecutive_loss_extends_to_sixty_minutes(monkeypatch):
    monkeypatch.setenv("PAPER_CRYPTO_REENTRY_COOLDOWN_MINUTES", "10")
    monkeypatch.setenv("PAPER_CRYPTO_LOSS_REENTRY_COOLDOWN_MULTIPLIER", "3")
    monkeypatch.setenv("PAPER_CRYPTO_CONSECUTIVE_LOSS_COOLDOWN_STEP", "0.5")
    _patch_history(monkeypatch, sell_age_minutes=50, streak=3)

    allowed, reason = guard._allow_generic_buy({"symbol": "NEAR-USD", "action": "BUY"})

    assert allowed is False
    assert "streak=3" in reason
    assert reason.endswith("/60.00m")


def test_positive_exit_still_uses_normal_ten_minute_cooldown(monkeypatch):
    monkeypatch.setenv("PAPER_CRYPTO_REENTRY_COOLDOWN_MINUTES", "10")
    _patch_history(monkeypatch, sell_age_minutes=12, sell_price=101.0, buy_price=100.0, streak=4)

    assert guard._allow_generic_buy({"symbol": "BNB-USD", "action": "BUY"}) == (
        True,
        "reentry_cooldown_elapsed",
    )


def test_live_or_broker_modes_cannot_activate_guard(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "true")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")
    assert guard._paper_only() is False

    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "true")
    assert guard._paper_only() is False
