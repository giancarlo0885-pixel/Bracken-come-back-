from datetime import datetime, timedelta, timezone

import paper_crypto_churn_guard as guard


def test_default_loss_reentry_cooldown_is_thirty_minutes(monkeypatch):
    monkeypatch.delenv("PAPER_CRYPTO_LOSS_REENTRY_COOLDOWN_MULTIPLIER", raising=False)
    monkeypatch.setenv("PAPER_CRYPTO_REENTRY_COOLDOWN_MINUTES", "10")

    now = datetime.now(timezone.utc)
    sell_time = now - timedelta(minutes=25)
    buy_time = sell_time - timedelta(minutes=15)

    def fake_last_trade(symbol, side):
        if side == "SELL":
            return {"created_at": sell_time, "price": 99.0}
        return {"created_at": buy_time, "price": 100.0}

    monkeypatch.setattr(guard, "_last_trade", fake_last_trade)
    allowed, reason = guard._allow_generic_buy({"symbol": "SOL-USD", "action": "BUY", "price": 100.0})

    assert allowed is False
    assert reason.startswith("loss_reentry_cooldown:")
    assert reason.endswith("/30.00m")
