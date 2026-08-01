from __future__ import annotations

from datetime import datetime, timezone

from market_sessions import (
    latest_completed_trading_day,
    market_session_open,
    quote_is_fresh,
)


def test_summer_daylight_saving_market_hours():
    assert market_session_open(datetime(2026, 7, 1, 13, 29, tzinfo=timezone.utc)) is False
    assert market_session_open(datetime(2026, 7, 1, 13, 30, tzinfo=timezone.utc)) is True


def test_winter_daylight_saving_market_hours():
    assert market_session_open(datetime(2026, 1, 2, 14, 29, tzinfo=timezone.utc)) is False
    assert market_session_open(datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)) is True


def test_market_holiday_uses_previous_completed_session():
    holiday = datetime(2026, 7, 3, 16, 0, tzinfo=timezone.utc)
    assert market_session_open(holiday) is False
    assert latest_completed_trading_day(holiday).isoformat() == "2026-07-02"
    assert quote_is_fresh("2026-07-02T20:00:00+00:00", "1d", holiday) is True
