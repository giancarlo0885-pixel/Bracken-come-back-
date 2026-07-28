from __future__ import annotations

import csv
import io
import logging
import os
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
    }


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