from datetime import datetime, timezone

import paper_weekly_risk_period_runtime as runtime


def test_installer_patches_weekly_period_only(monkeypatch):
    import oracle_bot

    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")

    original = oracle_bot._period_start
    try:
        assert runtime.install_paper_weekly_risk_period() is True
        weekly = datetime.fromisoformat(oracle_bot._period_start(7))
        now = datetime.now(timezone.utc)
        assert weekly.tzinfo is not None
        assert weekly.weekday() == 0
        assert weekly.hour == weekly.minute == weekly.second == 0
        assert 0 <= (now - weekly).total_seconds() < 7 * 24 * 3600

        # Non-week helper behavior remains delegated to the original function.
        assert oracle_bot._period_start(1) != ""
    finally:
        oracle_bot._period_start = original


def test_installer_fails_closed_when_live_not_disarmed(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "true")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")
    assert runtime.install_paper_weekly_risk_period() is False
