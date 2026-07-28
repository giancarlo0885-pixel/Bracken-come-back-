from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import requests

from api_manager import get_api_settings


log = logging.getLogger("economic-calendar")

EODHD_BASE_URL = "https://eodhd.com/api"
FINNHUB_BASE_URL = "https://finnhub.io/api/v1"


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


def _date_range() -> tuple[str, str]:
    today = date.today()
    end_date = today + timedelta(days=7)

    return (
        today.isoformat(),
        end_date.isoformat(),
    )


def _normalize_eodhd_event(
    item: dict[str, Any],
) -> dict[str, Any]:
    return {
        "date": (
            item.get("date")
            or item.get("datetime")
        ),
        "country": item.get("country"),
        "event": (
            item.get("event")
            or item.get("type")
            or item.get("name")
        ),
        "impact": (
            item.get("impact")
            or item.get("importance")
        ),
        "actual": item.get("actual"),
        "forecast": (
            item.get("forecast")
            or item.get("estimate")
        ),
        "previous": (
            item.get("previous")
            or item.get("prev")
        ),
        "provider": "EODHD",
    }


def _fetch_eodhd(
    api_key: str,
) -> ProviderResult:
    from_date, to_date = _date_range()

    response = requests.get(
        f"{EODHD_BASE_URL}/economic-events",
        params={
            "api_token": api_key,
            "fmt": "json",
            "from": from_date,
            "to": to_date,
        },
        timeout=20,
    )

    try:
        payload = response.json()
    except ValueError:
        payload = None

    if response.status_code in {401, 403}:
        return ProviderResult(
            available=False,
            provider="EODHD",
            records=[],
            message=(
                "EODHD rejected the key or this account "
                "does not include Economic Events access."
            ),
        )

    if response.status_code == 429:
        return ProviderResult(
            available=False,
            provider="EODHD",
            records=[],
            message="EODHD request limit reached.",
        )

    if not response.ok:
        return ProviderResult(
            available=False,
            provider="EODHD",
            records=[],
            message=(
                f"EODHD request failed with HTTP "
                f"{response.status_code}."
            ),
        )

    if isinstance(payload, dict):
        events = (
            payload.get("events")
            or payload.get("data")
            or payload.get("economicEvents")
            or []
        )
    elif isinstance(payload, list):
        events = payload
    else:
        events = []

    records = [
        _normalize_eodhd_event(item)
        for item in events
        if isinstance(item, dict)
    ]

    records.sort(
        key=lambda item: str(
            item.get("date") or ""
        )
    )

    return ProviderResult(
        available=True,
        provider="EODHD",
        records=records,
        message=(
            f"Loaded {len(records)} economic events "
            f"for the next seven days."
            if records
            else (
                "EODHD connected successfully, but no "
                "economic events were returned."
            )
        ),
    )


def _normalize_finnhub_event(
    item: dict[str, Any],
) -> dict[str, Any]:
    return {
        "date": (
            item.get("time")
            or item.get("date")
        ),
        "country": item.get("country"),
        "event": item.get("event"),
        "impact": item.get("impact"),
        "actual": item.get("actual"),
        "forecast": item.get("estimate"),
        "previous": item.get("prev"),
        "provider": "Finnhub",
    }


def _fetch_finnhub(
    api_key: str,
) -> ProviderResult:
    from_date, to_date = _date_range()

    response = requests.get(
        f"{FINNHUB_BASE_URL}/calendar/economic",
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

    if response.status_code in {401, 403}:
        return ProviderResult(
            available=False,
            provider="Finnhub",
            records=[],
            message=(
                "Finnhub economic calendar requires "
                "Premium access or the key was rejected."
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
        "economicCalendar",
        [],
    )

    if not isinstance(events, list):
        events = []

    records = [
        _normalize_finnhub_event(item)
        for item in events
        if isinstance(item, dict)
    ]

    records.sort(
        key=lambda item: str(
            item.get("date") or ""
        )
    )

    return ProviderResult(
        available=True,
        provider="Finnhub",
        records=records,
        message=(
            f"Loaded {len(records)} economic events."
            if records
            else (
                "Finnhub connected successfully, but no "
                "economic events were returned."
            )
        ),
    )


def fetch() -> ProviderResult:
    eodhd_key = _get_key(
        "EODHD_API_KEY",
        "EOD_API_KEY",
        "EODHD_TOKEN",
    )

    finnhub_key = _get_key(
        "FINNHUB_API_KEY",
    )

    if eodhd_key:
        try:
            result = _fetch_eodhd(
                eodhd_key
            )

            log.info(
                "Economic calendar provider=%s records=%d",
                result.provider,
                len(result.records),
            )

            if result.records:
                return result

            # Keep trying Finnhub when EODHD returns no data
            # or the current plan does not include the endpoint.
            eodhd_result = result

        except Exception as exc:
            log.warning(
                "EODHD economic calendar failed: %s",
                exc,
            )

            eodhd_result = ProviderResult(
                available=False,
                provider="EODHD",
                records=[],
                message=str(exc),
            )
    else:
        eodhd_result = None

    if finnhub_key:
        try:
            result = _fetch_finnhub(
                finnhub_key
            )

            log.info(
                "Economic calendar provider=%s records=%d",
                result.provider,
                len(result.records),
            )

            if result.records:
                return result

            if not eodhd_result:
                return result

        except Exception as exc:
            log.warning(
                "Finnhub economic calendar failed: %s",
                exc,
            )

    if eodhd_result:
        return eodhd_result

    return ProviderResult(
        available=False,
        provider="Not configured",
        records=[],
        message=(
            "Add EODHD_API_KEY or FINNHUB_API_KEY "
            "to Railway variables."
        ),
    )