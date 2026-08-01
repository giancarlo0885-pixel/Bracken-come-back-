from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from market_sessions import (
    completed_daily_bar_is_fresh,
    latest_completed_trading_day,
    latest_valid_bar_timestamp,
    local_time,
    market_session_open,
    quote_is_fresh,
)


def test_summer_daylight_saving_market_hours():
    assert market_session_open(datetime(2026, 7, 1, 13, 29, tzinfo=timezone.utc), "NASDAQ") is False
    assert market_session_open(datetime(2026, 7, 1, 13, 30, tzinfo=timezone.utc), "NASDAQ") is True


def test_winter_daylight_saving_market_hours():
    assert market_session_open(datetime(2026, 1, 2, 14, 29, tzinfo=timezone.utc), "NASDAQ") is False
    assert market_session_open(datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc), "NASDAQ") is True


def test_market_holiday_uses_previous_completed_session():
    holiday = datetime(2026, 7, 3, 16, 0, tzinfo=timezone.utc)
    assert market_session_open(holiday, "NASDAQ") is False
    assert latest_completed_trading_day(holiday, "NASDAQ").isoformat() == "2026-07-02"
    assert quote_is_fresh("2026-07-02T20:00:00+00:00", "1d", holiday, exchange="NASDAQ") is True


def test_european_market_open_on_us_holiday():
    us_holiday = datetime(2026, 7, 3, 8, 30, tzinfo=timezone.utc)
    assert market_session_open(us_holiday, "NASDAQ") is False
    assert market_session_open(us_holiday, "XETRA") is True


def test_japanese_daily_bar_during_asian_session_uses_prior_completed_day():
    tokyo_session = datetime(2026, 8, 3, 1, 0, tzinfo=timezone.utc)
    assert market_session_open(tokyo_session, "TSE") is True
    assert completed_daily_bar_is_fresh("2026-07-31T06:00:00+00:00", tokyo_session, exchange="TSE") is True
    assert quote_is_fresh("2026-08-03T00:59:00+00:00", "1m", tokyo_session, exchange="TSE") is True


def test_london_holiday_uses_lse_calendar_not_us_calendar():
    boxing_day = datetime(2026, 12, 28, 12, 0, tzinfo=timezone.utc)
    assert market_session_open(boxing_day, "LSE") is False
    assert latest_completed_trading_day(boxing_day, "LSE").isoformat() == "2026-12-24"


def test_different_exchange_timezones():
    instant = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
    assert local_time(instant, "NASDAQ").hour == 10
    assert local_time(instant, "TSE").hour == 23


def test_unknown_foreign_exchange_uses_conservative_age_rule():
    now = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
    assert quote_is_fresh("2026-08-03T13:50:00+00:00", "1d", now, exchange="UNKNOWN") is True
    assert quote_is_fresh("2026-08-02T13:50:00+00:00", "1d", now, exchange="UNKNOWN") is False


def test_yfinance_us_daily_date_only_stays_new_york_session_date():
    frame = pd.DataFrame({"Close": [100]}, index=pd.DatetimeIndex(["2026-07-31"]))
    bar = latest_valid_bar_timestamp(frame, "1d", exchange="NASDAQ", symbol="AAPL")
    assert bar is not None
    assert bar.session_date.isoformat() == "2026-07-31"
    assert bar.timestamp.isoformat() == "2026-07-31T20:00:00+00:00"


def test_london_daily_date_only_stays_london_session_date():
    frame = pd.DataFrame({"Close": [100]}, index=pd.DatetimeIndex(["2026-07-31"]))
    bar = latest_valid_bar_timestamp(frame, "1d", exchange="LSE", symbol="HSBA.L")
    assert bar is not None
    assert bar.session_date.isoformat() == "2026-07-31"
    assert bar.timestamp.isoformat() == "2026-07-31T15:30:00+00:00"


def test_tokyo_daily_date_only_stays_tokyo_session_date():
    frame = pd.DataFrame({"Close": [100]}, index=pd.DatetimeIndex(["2026-07-31"]))
    bar = latest_valid_bar_timestamp(frame, "1d", exchange="TSE", symbol="7203.T")
    assert bar is not None
    assert bar.session_date.isoformat() == "2026-07-31"
    assert bar.timestamp.isoformat() == "2026-07-31T06:00:00+00:00"


def test_ambiguous_naive_intraday_timestamp_is_rejected():
    frame = pd.DataFrame({"Close": [100]}, index=pd.DatetimeIndex(["2026-07-31 10:15"]))
    assert latest_valid_bar_timestamp(frame, "1m", exchange="NASDAQ", symbol="AAPL") is None
