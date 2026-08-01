from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo


NY_TZ = ZoneInfo("America/New_York")
OTC_EXCHANGES = {"OTC", "PINK", "OTCQX", "OTCQB", "GREY"}
EXCHANGE_ALIASES = {
    "": "",
    "US": "",
    "USA": "",
    "UNITED STATES": "",
    "NASDAQ": "NASDAQ",
    "NAS": "NASDAQ",
    "XNAS": "NASDAQ",
    "NYSE": "NYSE",
    "NYS": "NYSE",
    "XNYS": "NYSE",
    "AMEX": "NYSEAMERICAN",
    "NYSEAMERICAN": "NYSEAMERICAN",
    "NYSE AMERICAN": "NYSEAMERICAN",
    "ARCX": "NYSEARCA",
    "NYSEARCA": "NYSEARCA",
    "NYSE ARCA": "NYSEARCA",
    "BATS": "CBOE",
    "CBOE": "CBOE",
    "OTC": "OTC",
    "PINK": "PINK",
    "OTCQX": "OTCQX",
    "OTCQB": "OTCQB",
    "GREY": "GREY",
    "GREY MARKET": "GREY",
}


def parse_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_exchange(value: Any) -> str:
    text = str(value or "").strip().upper()
    return EXCHANGE_ALIASES.get(text, text)


def is_otc_exchange(value: Any) -> bool:
    return normalize_exchange(value) in OTC_EXCHANGES


def confirmed_us_listing(value: Any) -> bool:
    return normalize_exchange(value) in {"NASDAQ", "NYSE", "NYSEAMERICAN", "NYSEARCA", "CBOE"}


def ny_time(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(NY_TZ)


def _observed(month: int, day: int, year: int) -> date:
    actual = date(year, month, day)
    if actual.weekday() == 5:
        return actual - timedelta(days=1)
    if actual.weekday() == 6:
        return actual + timedelta(days=1)
    return actual


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    current = date(year, month, 1)
    while current.weekday() != weekday:
        current += timedelta(days=1)
    return current + timedelta(days=7 * (nth - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    current = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    while current.weekday() != weekday:
        current -= timedelta(days=1)
    return current


def _easter(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


@lru_cache(maxsize=16)
def fallback_market_holidays(year: int) -> set[date]:
    return {
        _observed(1, 1, year),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _easter(year) - timedelta(days=2),
        _last_weekday(year, 5, 0),
        _observed(6, 19, year),
        _observed(7, 4, year),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed(12, 25, year),
    }


def _calendar_open(day: date) -> bool | None:
    try:
        import pandas_market_calendars as mcal

        calendar = mcal.get_calendar("XNYS")
        schedule = calendar.schedule(start_date=day.isoformat(), end_date=day.isoformat())
        return not schedule.empty
    except Exception:
        return None


def is_trading_day(day: date) -> bool:
    calendar_result = _calendar_open(day)
    if calendar_result is not None:
        return calendar_result
    return day.weekday() < 5 and day not in fallback_market_holidays(day.year)


def market_session_open(now: datetime | None = None) -> bool:
    current = ny_time(now)
    if not is_trading_day(current.date()):
        return False
    minutes = current.hour * 60 + current.minute
    return (9 * 60 + 30) <= minutes < (16 * 60)


def latest_completed_trading_day(now: datetime | None = None) -> date:
    current = ny_time(now)
    if current.time() >= datetime(current.year, current.month, current.day, 16, 0, tzinfo=NY_TZ).time() and is_trading_day(current.date()):
        return current.date()
    day = current.date() - timedelta(days=1)
    while not is_trading_day(day):
        day -= timedelta(days=1)
    return day


def quote_freshness_seconds(quote_time: Any, now: datetime | None = None) -> float | None:
    quote_dt = parse_utc(quote_time)
    if quote_dt is None:
        return None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0.0, (current.astimezone(timezone.utc) - quote_dt).total_seconds())


def quote_is_fresh(
    quote_time: Any,
    interval: str = "1d",
    now: datetime | None = None,
    *,
    max_intraday_age_seconds: int = 120,
) -> bool:
    quote_dt = parse_utc(quote_time)
    if quote_dt is None:
        return False
    interval_text = str(interval or "").lower()
    if interval_text.endswith("m") or interval_text.endswith("h"):
        age = quote_freshness_seconds(quote_dt, now)
        return age is not None and age <= max(1, max_intraday_age_seconds)
    current_ny = ny_time(now)
    if market_session_open(now):
        return quote_dt.date() == current_ny.date()
    return quote_dt.date() == latest_completed_trading_day(now)
