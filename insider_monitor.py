from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import requests

from config import WATCHLISTS


log = logging.getLogger("insider-monitor")

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "").strip()
FINNHUB_BASE_URL = "https://finnhub.io/api/v1"

MAX_SYMBOLS_PER_SCAN = int(
    os.getenv("FINNHUB_INSIDER_MAX_SYMBOLS", "12")
)

MAX_RECORDS_PER_SYMBOL = int(
    os.getenv("FINNHUB_INSIDER_RECORDS_PER_SYMBOL", "5")
)


@dataclass
class Result:
    available: bool
    provider: str
    records: list[dict[str, Any]]
    message: str


def _stock_symbols() -> list[str]:
    symbols: list[str] = []

    for market_name in ("cash", "stock"):
        market_symbols = WATCHLISTS.get(market_name, [])

        for symbol in market_symbols:
            clean_symbol = str(symbol).strip().upper()

            if not clean_symbol:
                continue

            if clean_symbol.endswith("-USD"):
                continue

            if clean_symbol not in symbols:
                symbols.append(clean_symbol)

    return symbols[:MAX_SYMBOLS_PER_SCAN]


def _finnhub_get(
    endpoint: str,
    params: dict[str, Any] | None = None,
) -> Any:
    if not FINNHUB_API_KEY:
        raise RuntimeError(
            "FINNHUB_API_KEY is not configured"
        )

    response = requests.get(
        f"{FINNHUB_BASE_URL}/{endpoint.lstrip('/')}",
        params=params or {},
        headers={
            "X-Finnhub-Token": FINNHUB_API_KEY,
            "Accept": "application/json",
        },
        timeout=20,
    )

    if response.status_code == 401:
        raise RuntimeError(
            "Finnhub rejected the API key: 401 Unauthorized"
        )

    if response.status_code == 403:
        raise RuntimeError(
            "Finnhub denied access: 403 Forbidden"
        )

    if response.status_code == 429:
        raise RuntimeError(
            "Finnhub rate limit reached: 429"
        )

    response.raise_for_status()

    return response.json()


def _normalize_transaction(
    symbol: str,
    item: dict[str, Any],
) -> dict[str, Any]:
    change = item.get("change")
    transaction_price = item.get("transactionPrice")

    try:
        numeric_change = float(change or 0)
    except (TypeError, ValueError):
        numeric_change = 0.0

    direction = "BUY" if numeric_change > 0 else "SELL"

    return {
        "symbol": symbol,
        "name": item.get("name") or "Unknown insider",
        "side": direction,
        "change": numeric_change,
        "shares_owned": item.get("share"),
        "transaction_price": transaction_price,
        "transaction_code": item.get("transactionCode"),
        "transaction_date": item.get("transactionDate"),
        "filing_date": item.get("filingDate"),
        "provider": "Finnhub",
    }


def fetch() -> Result:
    if not FINNHUB_API_KEY:
        return Result(
            available=False,
            provider="Not configured",
            records=[],
            message=(
                "Add FINNHUB_API_KEY to Railway variables."
            ),
        )

    symbols = _stock_symbols()

    if not symbols:
        return Result(
            available=False,
            provider="Finnhub",
            records=[],
            message="No stock symbols were found in WATCHLISTS.",
        )

    records: list[dict[str, Any]] = []
    errors: list[str] = []

    for symbol in symbols:
        try:
            payload = _finnhub_get(
                "stock/insider-transactions",
                {
                    "symbol": symbol,
                },
            )

            if not isinstance(payload, dict):
                continue

            transactions = payload.get("data", [])

            if not isinstance(transactions, list):
                continue

            for item in transactions[
                :MAX_RECORDS_PER_SYMBOL
            ]:
                if not isinstance(item, dict):
                    continue

                records.append(
                    _normalize_transaction(
                        symbol,
                        item,
                    )
                )

        except Exception as exc:
            errors.append(f"{symbol}: {exc}")
            log.warning(
                "Finnhub insider request failed for %s: %s",
                symbol,
                exc,
            )

    records.sort(
        key=lambda record: (
            str(record.get("transaction_date") or ""),
            str(record.get("filing_date") or ""),
        ),
        reverse=True,
    )

    log.info(
        "Finnhub insider monitor returned %d records "
        "from %d symbols",
        len(records),
        len(symbols),
    )

    if records:
        return Result(
            available=True,
            provider="Finnhub",
            records=records,
            message=(
                f"Loaded {len(records)} insider transactions "
                f"across {len(symbols)} symbols."
            ),
        )

    if errors:
        return Result(
            available=False,
            provider="Finnhub",
            records=[],
            message=(
                "No insider records returned. "
                + " | ".join(errors[:3])
            ),
        )

    return Result(
        available=True,
        provider="Finnhub",
        records=[],
        message=(
            "Finnhub connected successfully, but no insider "
            "transactions were returned for this scan."
        ),
    )