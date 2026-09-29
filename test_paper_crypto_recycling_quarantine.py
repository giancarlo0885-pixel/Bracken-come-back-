from datetime import datetime, timedelta, timezone

import paper_crypto_churn_guard as guard


def _records(values, age_minutes=5):
    now = datetime.now(timezone.utc)
    return [
        {"created_at": now - timedelta(minutes=age_minutes + i), "realized_pnl": value}
        for i, value in enumerate(values)
    ]


def test_repeated_losing_symbol_is_quarantined(monkeypatch):
    monkeypatch.setenv("PAPER_CRYPTO_RECYCLE_MIN_TRADES", "5")
    monkeypatch.setenv("PAPER_CRYPTO_RECYCLE_LOSS_RATIO", "0.80")
    monkeypatch.setenv("PAPER_CRYPTO_RECYCLE_QUARANTINE_MINUTES", "180")
    monkeypatch.setattr(guard, "_recent_completed_round_trips", lambda symbol, limit=6: _records([-1, -1, -1, -1, 0.2]))
    allowed, reason = guard._recycling_quarantine("SOL-USD")
    assert allowed is False
    assert reason.startswith("recycling_quarantine:losses=4/5:")


def test_profitable_or_mixed_symbol_is_not_quarantined(monkeypatch):
    monkeypatch.setattr(guard, "_recent_completed_round_trips", lambda symbol, limit=6: _records([1, 1, -1, -1, 1]))
    assert guard._recycling_quarantine("AAVE-USD") == (True, "recycle_economics_not_losing")


def test_insufficient_evidence_does_not_block_exploration(monkeypatch):
    monkeypatch.setattr(guard, "_recent_completed_round_trips", lambda symbol, limit=6: _records([-1, -1]))
    assert guard._recycling_quarantine("LINK-USD") == (True, "recycle_evidence_immature")


def test_missing_realized_pnl_fails_open(monkeypatch):
    rows = _records([-1, -1, -1, -1, -1])
    rows[2]["realized_pnl"] = None
    monkeypatch.setattr(guard, "_recent_completed_round_trips", lambda symbol, limit=6: rows)
    assert guard._recycling_quarantine("SOL-USD") == (True, "recycle_realized_pnl_unavailable")


def test_quarantine_expires(monkeypatch):
    monkeypatch.setenv("PAPER_CRYPTO_RECYCLE_QUARANTINE_MINUTES", "180")
    monkeypatch.setattr(guard, "_recent_completed_round_trips", lambda symbol, limit=6: _records([-1, -1, -1, -1, -1], age_minutes=181))
    assert guard._recycling_quarantine("SOL-USD") == (True, "recycling_quarantine_elapsed")
