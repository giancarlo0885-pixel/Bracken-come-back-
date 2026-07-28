from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests

from api_manager import get_api_settings


log = logging.getLogger("congress-monitor")

QUIVER_URL = (
    "https://api.quiverquant.com/"
    "beta/live/congresstrading"
)

MAX_RECORDS = int(
    os.getenv("CONGRESS_MAX_RECORDS", "100")
)


@dataclass
class ProviderResult:
    available: bool
    provider: str
    records: list[dict[str, Any]]
    message: str


def _get_api_key() -> str:
    key = os.getenv(
        "QUIVER_API_KEY",
        "",
    ).strip()

    if key:
        return key

    try:
        settings = get_api_settings()

        return str(
            settings.values.get(
                "QUIVER_API_KEY",
                "",
            )
        ).strip()

    except Exception as exc:
        log.warning(
            "Could not read Quiver API settings: %s",
            exc,
        )

        return ""


def _first(
    item: dict[str, Any],
    *names: str,
) -> Any:
    for name in names:
        value = item.get(name)

        if value not in (
            None,
            "",
        ):
            return value

    return None


def _safe_date(value: Any) -> str | None:
    if value in (
        None,
        "",
    ):
        return None

    text = str(value).strip()

    if not text:
        return None

    try:
        cleaned = text.replace(
            "Z",
            "+00:00",
        )

        parsed = datetime.fromisoformat(
            cleaned
        )

        return parsed.date().isoformat()

    except ValueError:
        return text[:10]


def _normalize_transaction(
    value: Any,
) -> str | None:
    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    lowered = text.lower()

    if "purchase" in lowered:
        return "Purchase"

    if "sale" in lowered:
        return "Sale"

    if lowered == "buy":
        return "Purchase"

    if lowered == "sell":
        return "Sale"

    return text


def _normalize_record(
    item: dict[str, Any],
) -> dict[str, Any]:
    representative = _first(
        item,
        "Representative",
        "representative",
        "Politician",
        "politician",
        "Name",
        "name",
    )

    chamber = _first(
        item,
        "House",
        "house",
        "Chamber",
        "chamber",
    )

    transaction = _normalize_transaction(
        _first(
            item,
            "Transaction",
            "transaction",
            "TransactionType",
            "transaction_type",
            "Type",
            "type",
        )
    )

    return {
        "representative": representative,
        "party": _first(
            item,
            "Party",
            "party",
        ),
        "chamber": chamber,
        "state": _first(
            item,
            "State",
            "state",
        ),
        "district": _first(
            item,
            "District",
            "district",
        ),
        "symbol": _first(
            item,
            "Ticker",
            "ticker",
            "Symbol",
            "symbol",
        ),
        "company": _first(
            item,
            "Company",
            "company",
            "Description",
            "description",
            "Asset",
            "asset",
        ),
        "transaction": transaction,
        "amount": _first(
            item,
            "Range",
            "range",
            "Amount",
            "amount",
            "TradeSize",
            "trade_size",
        ),
        "transaction_date": _safe_date(
            _first(
                item,
                "TransactionDate",
                "transaction_date",
                "Traded",
                "traded",
                "Date",
                "date",
            )
        ),
        "filed_date": _safe_date(
            _first(
                item,
                "ReportDate",
                "report_date",
                "Filed",
                "filed",
                "DisclosureDate",
                "disclosure_date",
            )
        ),
        "owner": _first(
            item,
            "Owner",
            "owner",
        ),
        "source": _first(
            item,
            "Source",
            "source",
            "URL",
            "url",
        ),
        "provider": "Quiver Quantitative",
    }


def _sort_key(
    record: dict[str, Any],
) -> tuple[str, str]:
    return (
        str(
            record.get("filed_date")
            or record.get("transaction_date")
            or ""
        ),
        str(record.get("symbol") or ""),
    )


def fetch() -> ProviderResult:
    api_key = _get_api_key()

    if not api_key:
        return ProviderResult(
            available=False,
            provider="Not configured",
            records=[],
            message=(
                "Add QUIVER_API_KEY to Railway variables. "
                "Congress trading access may require a "
                "paid Quiver API subscription."
            ),
        )

    try:
        response = requests.get(
            QUIVER_URL,
            headers={
                "Authorization": f"Token {api_key}",
                "Accept": "application/json",
            },
            timeout=25,
        )

    except requests.RequestException as exc:
        log.exception(
            "Quiver Congress request failed"
        )

        return ProviderResult(
            available=False,
            provider="Quiver Quantitative",
            records=[],
            message=(
                "Could not connect to Quiver: "
                f"{exc}"
            ),
        )

    if response.status_code == 401:
        return ProviderResult(
            available=False,
            provider="Quiver Quantitative",
            records=[],
            message=(
                "Quiver rejected the API key. Check the "
                "QUIVER_API_KEY Railway variable."
            ),
        )

    if response.status_code == 403:
        return ProviderResult(
            available=False,
            provider="Quiver Quantitative",
            records=[],
            message=(
                "The Quiver key is valid, but this account "
                "does not include Congress Trading access."
            ),
        )

    if response.status_code == 429:
        return ProviderResult(
            available=False,
            provider="Quiver Quantitative",
            records=[],
            message=(
                "Quiver request limit reached. Try again "
                "after the provider limit resets."
            ),
        )

    if not response.ok:
        message = (
            f"Quiver request failed with HTTP "
            f"{response.status_code}."
        )

        try:
            payload = response.json()

            provider_message = (
                payload.get("detail")
                or payload.get("message")
                or payload.get("error")
            )

            if provider_message:
                message = (
                    f"{message} {provider_message}"
                )

        except ValueError:
            pass

        return ProviderResult(
            available=False,
            provider="Quiver Quantitative",
            records=[],
            message=message,
        )

    try:
        payload = response.json()

    except ValueError:
        return ProviderResult(
            available=False,
            provider="Quiver Quantitative",
            records=[],
            message=(
                "Quiver returned an invalid JSON response."
            ),
        )

    if isinstance(payload, dict):
        raw_records = (
            payload.get("data")
            or payload.get("results")
            or payload.get("records")
            or []
        )

    elif isinstance(payload, list):
        raw_records = payload

    else:
        raw_records = []

    records = [
        _normalize_record(item)
        for item in raw_records
        if isinstance(item, dict)
    ]

    records.sort(
        key=_sort_key,
        reverse=True,
    )

    records = records[:MAX_RECORDS]

    log.info(
        "Quiver Congress monitor returned %d records",
        len(records),
    )

    if records:
        purchases = sum(
            1
            for record in records
            if record.get("transaction")
            == "Purchase"
        )

        sales = sum(
            1
            for record in records
            if record.get("transaction")
            == "Sale"
        )

        return ProviderResult(
            available=True,
            provider="Quiver Quantitative",
            records=records,
            message=(
                f"Loaded {len(records)} recent congressional "
                f"trade disclosures: {purchases} purchases "
                f"and {sales} sales."
            ),
        )

    return ProviderResult(
        available=True,
        provider="Quiver Quantitative",
        records=[],
        message=(
            "Quiver connected successfully, but no recent "
            "congressional trades were returned."
        ),
    )