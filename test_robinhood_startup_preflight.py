from __future__ import annotations

import logging

import crypto_worker


def test_robinhood_startup_preflight_disabled(monkeypatch, caplog):
    monkeypatch.setenv("ROBINHOOD_CRYPTO_ENABLED", "false")
    with caplog.at_level(logging.INFO):
        crypto_worker.run_robinhood_startup_preflight()
    text = caplog.text
    assert "ROBINHOOD PREFLIGHT" in text
    assert "connection=DISABLED" in text
    assert "live_trading=DISARMED" in text
