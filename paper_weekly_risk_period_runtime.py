from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import os


log = logging.getLogger("paper-weekly-risk-period")


def _false_env(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() not in {"1", "true", "yes", "on"}


def install_paper_weekly_risk_period() -> bool:
    """Make the paper 'weekly' loss guard use the current UTC calendar week.

    The legacy implementation used ``now - 7 days``. That behaves as a rolling
    seven-day drawdown and can carry prior-week losses across Monday, even though
    the guard and operator-facing logs call it a weekly limit. This installer is
    deliberately paper-only and leaves the configured loss threshold unchanged.
    """
    if os.getenv("EXECUTION_MODE", "paper").strip().lower() != "paper":
        log.warning("PAPER WEEKLY RISK PERIOD | status=SKIP | reason=not_paper")
        return False
    if not _false_env("ENABLE_BROKER_SUBMISSION") or not _false_env("LIVE_TRADING_ARMED"):
        log.warning("PAPER WEEKLY RISK PERIOD | status=SKIP | reason=live_not_disarmed")
        return False

    import oracle_bot

    original = getattr(oracle_bot, "_period_start", None)
    if not callable(original):
        log.warning("PAPER WEEKLY RISK PERIOD | status=SKIP | reason=period_helper_missing")
        return False
    if getattr(original, "_paper_calendar_week_patch", False):
        return True

    def _calendar_period_start(days: int) -> str:
        if int(days) != 7:
            return original(days)
        now = datetime.now(timezone.utc)
        monday = (now - timedelta(days=now.weekday())).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        return monday.isoformat()

    _calendar_period_start._paper_calendar_week_patch = True  # type: ignore[attr-defined]
    oracle_bot._period_start = _calendar_period_start
    log.info(
        "PAPER WEEKLY RISK PERIOD | status=ACTIVE | mode=calendar_week_utc | threshold=UNCHANGED | live_trading=DISARMED"
    )
    return True
