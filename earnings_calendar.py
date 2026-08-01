from __future__ import annotations

import csv
import json
import io
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import requests

from api_manager import get_api_settings


log = logging.getLogger("earnings-calendar")

FINNHUB_BASE_URL = "https://finnhub.io/api/v1"
ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"

DAYS_AHEAD = int(
    os.getenv("EARNINGS_CALENDAR_DAYS_AHEAD", "14")
)

MAX_RECORDS = int(
    os.getenv("EARNINGS_CALENDAR_MAX_RECORDS", "100")
)

CORE_BLUE_CHIPS = {
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "BRK.B",
    "JPM", "JNJ", "V", "MA", "PG", "UNH", "HD", "XOM", "LLY", "AVGO",
}
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")


@dataclass
class ProviderResult:
    available: bool
    provider: str
    records: list[dict[str, Any]]
    message: str


def _get_key(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()

        if value:
            return value

    try:
        settings = get_api_settings()

        for name in names:
            value = str(
                settings.values.get(name, "")
            ).strip()

            if value:
                return value

    except Exception as exc:
        log.warning(
            "Could not read API settings: %s",
            exc,
        )

    return ""


def _calendar_dates() -> tuple[str, str]:
    start_date = date.today()
    end_date = start_date + timedelta(
        days=DAYS_AHEAD
    )

    return (
        start_date.isoformat(),
        end_date.isoformat(),
    )


def _safe_float(value: Any) -> float | None:
    try:
        if value in (
            None,
            "",
            "None",
            "null",
            "N/A",
        ):
            return None

        return float(value)

    except (TypeError, ValueError):
        return None


def _safe_money(value: Any, *, allow_zero: bool = False) -> float | None:
    number = _safe_float(value)
    if number is None:
        return None
    if number == 0 and not allow_zero:
        return None
    return number


def _normalize_finnhub_record(
    item: dict[str, Any],
) -> dict[str, Any]:
    return {
        "date": item.get("date"),
        "symbol": item.get("symbol"),
        "hour": item.get("hour"),
        "quarter": item.get("quarter"),
        "year": item.get("year"),
        "eps_estimate": _safe_float(
            item.get("epsEstimate")
        ),
        "eps_actual": _safe_float(
            item.get("epsActual")
        ),
        "revenue_estimate": _safe_float(
            item.get("revenueEstimate")
        ),
        "revenue_actual": _safe_float(
            item.get("revenueActual")
        ),
        "provider": "Finnhub",
        "raw_payload": item,
    }


def _fetch_finnhub(
    api_key: str,
) -> ProviderResult:
    from_date, to_date = _calendar_dates()

    response = requests.get(
        f"{FINNHUB_BASE_URL}/calendar/earnings",
        params={
            "from": from_date,
            "to": to_date,
        },
        headers={
            "X-Finnhub-Token": api_key,
            "Accept": "application/json",
        },
        timeout=20,
    )

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if response.status_code == 401:
        return ProviderResult(
            available=False,
            provider="Finnhub",
            records=[],
            message="Finnhub rejected the API key.",
        )

    if response.status_code == 403:
        return ProviderResult(
            available=False,
            provider="Finnhub",
            records=[],
            message=(
                "Finnhub denied access to the earnings "
                "calendar endpoint."
            ),
        )

    if response.status_code == 429:
        return ProviderResult(
            available=False,
            provider="Finnhub",
            records=[],
            message="Finnhub request limit reached.",
        )

    if not response.ok:
        return ProviderResult(
            available=False,
            provider="Finnhub",
            records=[],
            message=(
                f"Finnhub request failed with HTTP "
                f"{response.status_code}."
            ),
        )

    events = payload.get(
        "earningsCalendar",
        [],
    )

    if not isinstance(events, list):
        events = []

    records = [
        _normalize_finnhub_record(item)
        for item in events[:MAX_RECORDS]
        if isinstance(item, dict)
    ]

    records.sort(
        key=lambda record: (
            str(record.get("date") or ""),
            str(record.get("symbol") or ""),
        )
    )

    return ProviderResult(
        available=True,
        provider="Finnhub",
        records=records,
        message=(
            f"Loaded {len(records)} upcoming earnings "
            f"events for the next {DAYS_AHEAD} days."
            if records
            else (
                "Finnhub connected successfully, but no "
                "earnings events were returned."
            )
        ),
    )


def _normalize_alpha_vantage_record(
    item: dict[str, str],
) -> dict[str, Any]:
    return {
        "date": (
            item.get("reportDate")
            or item.get("report_date")
        ),
        "symbol": item.get("symbol"),
        "name": item.get("name"),
        "fiscal_date_ending": (
            item.get("fiscalDateEnding")
            or item.get("fiscal_date_ending")
        ),
        "eps_estimate": _safe_float(
            item.get("estimate")
            or item.get("epsEstimate")
        ),
        "currency": item.get("currency"),
        "provider": "Alpha Vantage",
        "raw_payload": item,
    }


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def validate_symbol(symbol: Any, exchange: Any = None) -> tuple[str, bool, str]:
    normalized = str(symbol or "").strip().upper()
    exchange_text = str(exchange or "").strip()
    if not normalized:
        return "", False, "missing symbol"
    if not SYMBOL_RE.match(normalized):
        return normalized, False, "invalid ticker format"
    if exchange_text and len(exchange_text) > 24:
        return normalized, False, "invalid exchange metadata"
    return normalized, True, "validated" if exchange_text else "validated without exchange metadata"


def format_revenue(value: Any, *, allow_zero: bool = False) -> str | None:
    number = _safe_money(value, allow_zero=allow_zero)
    if number is None:
        return None
    sign = "-" if number < 0 else ""
    magnitude = abs(number)
    for threshold, suffix in (
        (1_000_000_000, "B"),
        (1_000_000, "M"),
        (1_000, "K"),
    ):
        if magnitude >= threshold:
            compact = magnitude / threshold
            text = f"{compact:.1f}".rstrip("0").rstrip(".")
            return f"{sign}${text}{suffix}"
    return f"{sign}${magnitude:,.0f}"


def format_eps(value: Any) -> str | None:
    number = _safe_float(value)
    if number is None:
        return None
    if abs(number) >= 10:
        return f"{number:.2f}".rstrip("0").rstrip(".")
    return f"{number:.3f}".rstrip("0").rstrip(".")


def format_surprise(actual: Any, estimate: Any) -> str | None:
    actual_number = _safe_float(actual)
    estimate_number = _safe_float(estimate)
    if actual_number is None or estimate_number in (None, 0):
        return None
    surprise = ((actual_number - estimate_number) / abs(estimate_number)) * 100
    return f"{surprise:+.1f}%"


def normalize_hour(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"bmo", "before market open", "before market", "amc before"}:
        return "Before market"
    if text in {"amc", "after market close", "after market", "pmc"}:
        return "After market"
    return "Time not supplied"


def event_status(event_date: date | None, has_actual: bool, complete: bool, today: date | None = None) -> str:
    if not complete:
        return "Incomplete"
    current = today or date.today()
    if has_actual:
        return "Reported"
    if event_date and event_date < current:
        return "Past / Incomplete"
    if event_date == current:
        return "Reporting Today"
    return "Upcoming"


def _reported_zero(payload: dict[str, Any], *names: str) -> bool:
    for name in names:
        if bool(payload.get(f"{name}_reported_zero")) or bool(payload.get(f"{name}ReportedZero")):
            return True
    return False


def prepare_events(
    records: list[dict[str, Any]],
    *,
    held_symbols: set[str] | None = None,
    opportunity_symbols: set[str] | None = None,
    major_movers: set[str] | None = None,
    today: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    ticker_filter: str = "",
    market_cap_category: str = "All",
    reporting_today: bool = False,
    held_only: bool = False,
    opportunity_only: bool = False,
    limit: int = 20,
) -> dict[str, list[dict[str, Any]]]:
    held_symbols = {s.upper() for s in (held_symbols or set())}
    opportunity_symbols = {s.upper() for s in (opportunity_symbols or set())}
    major_movers = {s.upper() for s in (major_movers or set())}
    current = today or date.today()
    start = start_date or current
    end = end_date or (current + timedelta(days=DAYS_AHEAD))
    ticker_query = ticker_filter.strip().upper()
    seen: set[tuple[str, str, str]] = set()
    complete_events: list[dict[str, Any]] = []
    incomplete_events: list[dict[str, Any]] = []

    for source in records:
        payload = _payload(source.get("details")) or source
        symbol, symbol_ok, validation = validate_symbol(
            payload.get("symbol") or source.get("symbol"),
            payload.get("exchange") or payload.get("exchangeCode"),
        )
        event_date = _parse_date(payload.get("date") or payload.get("reportDate") or source.get("event_time"))
        quarter = str(payload.get("quarter") or payload.get("fiscalQuarter") or "").strip()
        key = (symbol, event_date.isoformat() if event_date else "", quarter)
        if key in seen:
            continue
        seen.add(key)

        if ticker_query and ticker_query not in symbol:
            continue
        if event_date and not (start <= event_date <= end):
            continue

        cap_category = str(payload.get("market_cap_category") or payload.get("marketCapCategory") or "Unknown")
        if market_cap_category != "All" and cap_category != market_cap_category:
            continue
        if held_only and symbol not in held_symbols:
            continue
        if opportunity_only and symbol not in opportunity_symbols:
            continue

        eps_estimate = _safe_float(payload.get("eps_estimate", payload.get("epsEstimate")))
        eps_actual = _safe_float(payload.get("eps_actual", payload.get("epsActual")))
        revenue_estimate = _safe_money(
            payload.get("revenue_estimate", payload.get("revenueEstimate")),
            allow_zero=_reported_zero(payload, "revenue_estimate", "revenueEstimate"),
        )
        raw_revenue_actual = payload.get("revenue_actual", payload.get("revenueActual"))
        revenue_actual_reported_zero = _reported_zero(payload, "revenue_actual", "revenueActual")
        questionable_zero_actual = _safe_float(raw_revenue_actual) == 0 and not revenue_actual_reported_zero
        revenue_actual = _safe_money(
            raw_revenue_actual,
            allow_zero=revenue_actual_reported_zero,
        )
        has_actual = eps_actual is not None or revenue_actual is not None
        complete = bool(symbol_ok and event_date and not questionable_zero_actual)
        status = event_status(event_date, has_actual, complete, current)
        if reporting_today and status != "Reporting Today":
            continue

        priority = 0
        priority += 100 if symbol in held_symbols else 0
        priority += 60 if symbol in CORE_BLUE_CHIPS else 0
        priority += 40 if symbol in major_movers else 0
        priority += 30 if symbol in opportunity_symbols else 0

        event = {
            "symbol": symbol or "Unknown",
            "company": payload.get("name") or payload.get("company") or payload.get("companyName"),
            "date": event_date.isoformat() if event_date else None,
            "hour": normalize_hour(payload.get("hour")),
            "eps_estimate": format_eps(eps_estimate),
            "eps_actual": format_eps(eps_actual),
            "eps_surprise_pct": format_surprise(eps_actual, eps_estimate),
            "revenue_estimate": format_revenue(revenue_estimate),
            "revenue_actual": format_revenue(revenue_actual, allow_zero=revenue_actual == 0),
            "revenue_surprise_pct": format_surprise(revenue_actual, revenue_estimate),
            "provider": payload.get("provider") or source.get("provider") or "Unknown",
            "status": status,
            "market_cap_category": cap_category,
            "exchange": payload.get("exchange") or payload.get("exchangeCode"),
            "validation": validation,
            "priority": priority,
            "raw_payload": payload,
        }
        if complete:
            complete_events.append(event)
        else:
            incomplete_events.append(event)

    complete_events.sort(key=lambda event: (event.get("date") or "9999-99-99", -int(event.get("priority") or 0), event.get("symbol") or ""))
    incomplete_events.sort(key=lambda event: (event.get("symbol") or "", event.get("date") or "9999-99-99"))
    return {
        "main": complete_events[:limit],
        "more": complete_events[limit:],
        "incomplete": incomplete_events,
    }


def table_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = (
        ("Ticker", "symbol"),
        ("Company", "company"),
        ("Date", "date"),
        ("Time", "hour"),
        ("EPS Est.", "eps_estimate"),
        ("EPS Actual", "eps_actual"),
        ("EPS Surprise", "eps_surprise_pct"),
        ("Rev. Est.", "revenue_estimate"),
        ("Rev. Actual", "revenue_actual"),
        ("Rev. Surprise", "revenue_surprise_pct"),
        ("Provider", "provider"),
        ("Status", "status"),
    )
    return [
        {label: event.get(key) for label, key in fields if event.get(key) not in (None, "")}
        for event in events
    ]


def mobile_card_lines(event: dict[str, Any]) -> list[str]:
    lines = [
        f"{event.get('symbol', 'Unknown')} - {event.get('company') or event.get('provider', 'Unknown')}",
        f"{event.get('date') or 'Date unavailable'} - {event.get('hour') or 'Time not supplied'}",
        f"Status: {event.get('status', 'Incomplete')}",
    ]
    for label, key in (
        ("EPS est.", "eps_estimate"),
        ("EPS actual", "eps_actual"),
        ("EPS surprise", "eps_surprise_pct"),
        ("Revenue est.", "revenue_estimate"),
        ("Revenue actual", "revenue_actual"),
        ("Revenue surprise", "revenue_surprise_pct"),
        ("Provider", "provider"),
    ):
        value = event.get(key)
        if value not in (None, ""):
            lines.append(f"{label}: {value}")
    return lines


def _fetch_alpha_vantage(
    api_key: str,
) -> ProviderResult:
    response = requests.get(
        ALPHA_VANTAGE_URL,
        params={
            "function": "EARNINGS_CALENDAR",
            "horizon": "3month",
            "apikey": api_key,
        },
        timeout=30,
    )

    if response.status_code == 401:
        return ProviderResult(
            available=False,
            provider="Alpha Vantage",
            records=[],
            message=(
                "Alpha Vantage rejected the API key."
            ),
        )

    if response.status_code == 429:
        return ProviderResult(
            available=False,
            provider="Alpha Vantage",
            records=[],
            message=(
                "Alpha Vantage request limit reached."
            ),
        )

    if not response.ok:
        return ProviderResult(
            available=False,
            provider="Alpha Vantage",
            records=[],
            message=(
                f"Alpha Vantage request failed with HTTP "
                f"{response.status_code}."
            ),
        )

    content_type = response.headers.get(
        "content-type",
        "",
    ).lower()

    if "application/json" in content_type:
        try:
            payload = response.json()
        except ValueError:
            payload = {}

        error_message = (
            payload.get("Error Message")
            or payload.get("Information")
            or payload.get("Note")
        )

        if error_message:
            return ProviderResult(
                available=False,
                provider="Alpha Vantage",
                records=[],
                message=str(error_message),
            )

    reader = csv.DictReader(
        io.StringIO(response.text)
    )

    rows = [
        row
        for row in reader
        if isinstance(row, dict)
        and row.get("symbol")
    ]

    today = date.today()
    end_date = today + timedelta(
        days=DAYS_AHEAD
    )

    filtered_rows: list[dict[str, str]] = []

    for row in rows:
        report_date_text = (
            row.get("reportDate")
            or row.get("report_date")
            or ""
        )

        try:
            report_date = date.fromisoformat(
                report_date_text
            )
        except ValueError:
            continue

        if today <= report_date <= end_date:
            filtered_rows.append(row)

    records = [
        _normalize_alpha_vantage_record(row)
        for row in filtered_rows[:MAX_RECORDS]
    ]

    records.sort(
        key=lambda record: (
            str(record.get("date") or ""),
            str(record.get("symbol") or ""),
        )
    )

    return ProviderResult(
        available=True,
        provider="Alpha Vantage",
        records=records,
        message=(
            f"Loaded {len(records)} upcoming earnings "
            f"events for the next {DAYS_AHEAD} days."
            if records
            else (
                "Alpha Vantage connected successfully, "
                "but no earnings events were returned "
                "for the selected period."
            )
        ),
    )


def fetch() -> ProviderResult:
    finnhub_key = _get_key(
        "FINNHUB_API_KEY",
    )

    alpha_vantage_key = _get_key(
        "ALPHA_VANTAGE_API_KEY",
        "ALPHAVANTAGE_API_KEY",
    )

    finnhub_result: ProviderResult | None = None

    if finnhub_key:
        try:
            finnhub_result = _fetch_finnhub(
                finnhub_key
            )

            log.info(
                "Earnings calendar provider=%s records=%d",
                finnhub_result.provider,
                len(finnhub_result.records),
            )

            if finnhub_result.records:
                return finnhub_result

        except Exception as exc:
            log.warning(
                "Finnhub earnings calendar failed: %s",
                exc,
            )

            finnhub_result = ProviderResult(
                available=False,
                provider="Finnhub",
                records=[],
                message=str(exc),
            )

    if alpha_vantage_key:
        try:
            alpha_result = _fetch_alpha_vantage(
                alpha_vantage_key
            )

            log.info(
                "Earnings calendar provider=%s records=%d",
                alpha_result.provider,
                len(alpha_result.records),
            )

            if alpha_result.records:
                return alpha_result

            if not finnhub_result:
                return alpha_result

        except Exception as exc:
            log.warning(
                "Alpha Vantage earnings calendar failed: %s",
                exc,
            )

    if finnhub_result:
        return finnhub_result

    return ProviderResult(
        available=False,
        provider="Not configured",
        records=[],
        message=(
            "Add FINNHUB_API_KEY or "
            "ALPHA_VANTAGE_API_KEY to Railway variables."
        ),
    )
