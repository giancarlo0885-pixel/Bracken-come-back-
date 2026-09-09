from types import SimpleNamespace

import paper_crypto_learning_relaxation as relaxation


def test_paper_limits_have_nonzero_bounds(monkeypatch):
    monkeypatch.delenv("PAPER_CRYPTO_MAX_DAILY_TURNOVER_PCT", raising=False)
    monkeypatch.delenv("PAPER_CRYPTO_MAX_DAILY_ENTRIES", raising=False)
    monkeypatch.delenv("PAPER_CRYPTO_ENTRY_COOLDOWN_MINUTES", raising=False)
    turnover, entries, cooldown = relaxation._paper_limits()
    assert turnover == 1.0
    assert entries == 48
    assert cooldown == 15


def test_paper_limits_are_bounded(monkeypatch):
    monkeypatch.setenv("PAPER_CRYPTO_MAX_DAILY_TURNOVER_PCT", "999")
    monkeypatch.setenv("PAPER_CRYPTO_MAX_DAILY_ENTRIES", "9999")
    monkeypatch.setenv("PAPER_CRYPTO_ENTRY_COOLDOWN_MINUTES", "999")
    turnover, entries, cooldown = relaxation._paper_limits()
    assert turnover == 5.0
    assert entries == 250
    assert cooldown == 120


def test_relaxation_cannot_activate_when_live_armed(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "true")
    assert relaxation._active() is False
