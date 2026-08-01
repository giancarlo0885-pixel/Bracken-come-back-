from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
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
    "L": "LSE",
    "XLON": "LSE",
    "LSE": "LSE",
    "DE": "XETRA",
    "FRA": "XETRA",
    "XETRA": "XETRA",
    "XETR": "XETRA",
    "T": "TSE",
    "JP": "TSE",
    "TSE": "TSE",
    "XTKS": "TSE",
    "HK": "HKEX",
    "HKG": "HKEX",
    "HKEX": "HKEX",
    "XHKG": "HKEX",
    "NS": "NSE",
    "NSE": "NSE",
    "XNSE": "NSE",
    "BO": "BSE",
    "BSE": "BSE",
    "AX": "ASX",
    "AU": "ASX",
    "ASX": "ASX",
    "XASX": "ASX",
    "TO": "TSX",
    "TSX": "TSX",
    "XTSE": "TSX",
    "V": "TSXV",
    "SA": "B3",
    "B3": "B3",
    "BVMF": "B3",
    "MX": "BMV",
    "BMV": "BMV",
}


@dataclass(frozen=True)
class ExchangeSession:
    exchange: str
    calendar: str | None
    timezone: str
    open_time: time
    close_time: time
    holiday_profile: str = "weekday"


EXCHANGE_SESSIONS: dict[str, ExchangeSession] = {
    "NASDAQ": ExchangeSession("NASDAQ", "NASDAQ", "America/New_York", time(9, 30), time(16), "us"),
    "NYSE": ExchangeSession("NYSE", "XNYS", "America/New_York", time(9, 30), time(16), "us"),
    "NYSEAMERICAN": ExchangeSession("NYSEAMERICAN", "XNYS", "America/New_York", time(9, 30), time(16), "us"),
    "NYSEARCA": ExchangeSession("NYSEARCA", "XNYS", "America/New_York", time(9, 30), time(16), "us"),
    "CBOE": ExchangeSession("CBOE", "NYSE", "America/New_York", time(9, 30), time(16), "us"),
    "LSE": ExchangeSession("LSE", "XLON", "Europe/London", time(8), time(16, 30), "uk"),
    "XETRA": ExchangeSession("XETRA", "XETR", "Europe/Berlin", time(9), time(17, 30), "weekday"),
    "TSE": ExchangeSession("TSE", "XTKS", "Asia/Tokyo", time(9), time(15), "japan"),
    "HKEX": ExchangeSession("HKEX", "XHKG", "Asia/Hong_Kong", time(9, 30), time(16), "weekday"),
    "NSE": ExchangeSession("NSE", "XNSE", "Asia/Kolkata", time(9, 15), time(15, 30), "weekday"),
    "BSE": ExchangeSession("BSE", "XBOM", "Asia/Kolkata", time(9, 15), time(15, 30), "weekday"),
    "ASX": ExchangeSession("ASX", "XASX", "Australia/Sydney", time(10), time(16), "weekday"),
    "TSX": ExchangeSession("TSX", "XTSE", "America/Toronto", time(9, 30), time(16), "canada"),
    "TSXV": ExchangeSession("TSXV", "XTSE", "America/Toronto", time(9, 30), time(16), "canada"),
    "B3": ExchangeSession("B3", "BVMF", "America/Sao_Paulo", time(10), time(17), "weekday"),
    "BMV": ExchangeSession("BMV", "BMV", "America/Mexico_City", time(8, 30), time(15), "weekday"),
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


def exchange_from_symbol(symbol: Any) -> str:
    text = str(symbol or "").upper().strip()
    suffix_map = {
        ".L": "LSE",
        ".DE": "XETRA",
        ".F": "XETRA",
        ".T": "TSE",
        ".HK": "HKEX",
        ".NS": "NSE",
        ".BO": "BSE",
        ".AX": "ASX",
        ".TO": "TSX",
        ".V": "TSXV",
        ".SA": "B3",
        ".MX": "BMV",
    }
    for suffix, exchange in suffix_map.items():
        if text.endswith(suffix):
            return exchange
    return ""


def resolve_exchange(exchange: Any = "", region: Any = "", symbol: Any = "") -> str:
    normalized = normalize_exchange(exchange)
    if normalized in EXCHANGE_SESSIONS or normalized in OTC_EXCHANGES:
        return normalized
    symbol_exchange = exchange_from_symbol(symbol)
    if symbol_exchange:
        return symbol_exchange
    region_text = str(region or "").strip().lower()
    region_map = {
        "united kingdom": "LSE",
        "europe": "XETRA",
        "japan": "TSE",
        "greater china": "HKEX",
        "india": "NSE",
        "australia": "ASX",
        "canada": "TSX",
        "latin america": "B3",
        "north america": "NYSE",
        "united states": "NYSE",
    }
    return region_map.get(region_text, normalized)


def is_otc_exchange(value: Any) -> bool:
    return normalize_exchange(value) in OTC_EXCHANGES


def confirmed_us_listing(value: Any) -> bool:
    return normalize_exchange(value) in {"NASDAQ", "NYSE", "NYSEAMERICAN", "NYSEARCA", "CBOE"}


def local_time(value: datetime | None = None, exchange: Any = "", region: Any = "", symbol: Any = "") -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    resolved = resolve_exchange(exchange, region, symbol)
    session = EXCHANGE_SESSIONS.get(resolved)
    zone = ZoneInfo(session.timezone if session else "UTC")
    return current.astimezone(zone)


def ny_time(value: datetime | None = None) -> datetime:
    return local_time(value, "NYSE")


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


@lru_cache(maxsize=32)
def fallback_profile_holidays(profile: str, year: int) -> set[date]:
    if profile == "us":
        return fallback_market_holidays(year)
    if profile == "uk":
        boxing_day = date(year, 12, 26)
        if boxing_day.weekday() == 5:
            boxing_observed = date(year, 12, 28)
        elif boxing_day.weekday() == 6:
            boxing_observed = date(year, 12, 27)
        else:
            boxing_observed = boxing_day
        return {
            _observed(1, 1, year),
            _easter(year) - timedelta(days=2),
            _easter(year) + timedelta(days=1),
            _last_weekday(year, 5, 0),
            _observed(12, 25, year),
            boxing_observed,
        }
    if profile == "japan":
        return {_observed(1, 1, year), _observed(2, 11, year), _observed(12, 31, year)}
    if profile == "canada":
        return {_observed(1, 1, year), _easter(year) - timedelta(days=2), _observed(7, 1, year), _observed(12, 25, year)}
    return set()


def _calendar_open(day: date, session: ExchangeSession) -> bool | None:
    if not session.calendar:
        return None
    try:
        import pandas_market_calendars as mcal

        calendar = mcal.get_calendar(session.calendar)
        schedule = calendar.schedule(start_date=day.isoformat(), end_date=day.isoformat())
        return not schedule.empty
    except Exception:
        return None


def is_trading_day(day: date, exchange: Any = "", region: Any = "", symbol: Any = "") -> bool:
    session = EXCHANGE_SESSIONS.get(resolve_exchange(exchange, region, symbol))
    if session is None:
        return day.weekday() < 5
    calendar_result = _calendar_open(day, session)
    if calendar_result is not None:
        return calendar_result
    return day.weekday() < 5 and day not in fallback_profile_holidays(session.holiday_profile, day.year)


def market_session_open(now: datetime | None = None, exchange: Any = "", region: Any = "", symbol: Any = "") -> bool:
    resolved = resolve_exchange(exchange, region, symbol)
    session = EXCHANGE_SESSIONS.get(resolved)
    if session is None:
        return False
    current = local_time(now, resolved)
    if not is_trading_day(current.date(), resolved):
        return False
    return session.open_time <= current.time() < session.close_time


def market_session_state(now: datetime | None = None, exchange: Any = "", region: Any = "", symbol: Any = "") -> str:
    resolved = resolve_exchange(exchange, region, symbol)
    session = EXCHANGE_SESSIONS.get(resolved)
    if session is None:
        return "unknown"
    current = local_time(now, resolved)
    if not is_trading_day(current.date(), resolved):
        return "closed"
    if current.time() < session.open_time:
        return "premarket"
    if current.time() < session.close_time:
        return "regular"
    return "after-hours"


def latest_completed_trading_day(now: datetime | None = None, exchange: Any = "", region: Any = "", symbol: Any = "") -> date:
    resolved = resolve_exchange(exchange, region, symbol)
    session = EXCHANGE_SESSIONS.get(resolved)
    current = local_time(now, resolved)
    if session and current.time() >= session.close_time and is_trading_day(current.date(), resolved):
        return current.date()
    day = current.date() - timedelta(days=1)
    while not is_trading_day(day, resolved):
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
    exchange: Any = "",
    region: Any = "",
    symbol: Any = "",
    max_unknown_age_seconds: int = 900,
) -> bool:
    quote_dt = parse_utc(quote_time)
    if quote_dt is None:
        return False
    interval_text = str(interval or "").lower()
    if interval_text.endswith("m") or interval_text.endswith("h"):
        age = quote_freshness_seconds(quote_dt, now)
        return age is not None and age <= max(1, max_intraday_age_seconds)
    resolved = resolve_exchange(exchange, region, symbol)
    if resolved not in EXCHANGE_SESSIONS:
        age = quote_freshness_seconds(quote_dt, now)
        return age is not None and age <= max_unknown_age_seconds
    current_local = local_time(now, resolved)
    if market_session_open(now, resolved):
        return quote_dt.astimezone(ZoneInfo(EXCHANGE_SESSIONS[resolved].timezone)).date() == current_local.date()
    return quote_dt.astimezone(ZoneInfo(EXCHANGE_SESSIONS[resolved].timezone)).date() == latest_completed_trading_day(now, resolved)


def completed_daily_bar_is_fresh(
    quote_time: Any,
    now: datetime | None = None,
    *,
    exchange: Any = "",
    region: Any = "",
    symbol: Any = "",
    max_unknown_age_seconds: int = 86_400,
) -> bool:
    quote_dt = parse_utc(quote_time)
    if quote_dt is None:
        return False
    resolved = resolve_exchange(exchange, region, symbol)
    if resolved not in EXCHANGE_SESSIONS:
        age = quote_freshness_seconds(quote_dt, now)
        return age is not None and age <= max_unknown_age_seconds
    zone = ZoneInfo(EXCHANGE_SESSIONS[resolved].timezone)
    return quote_dt.astimezone(zone).date() == latest_completed_trading_day(now, resolved)
