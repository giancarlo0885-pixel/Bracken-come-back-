from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import requests

from config import WATCHLISTS
from provider_guard import available as provider_available, disable as disable_provider, state as provider_guard_state


log = logging.getLogger("etf-flow")

FINNHUB_BASE_URL = "https://finnhub.io/api/v1"

MAX_ETFS = int(
    os.getenv("FINNHUB_ETF_MAX_SYMBOLS", "12")
)

MAX_HOLDINGS_PER_ETF = int(
    os.getenv("FINNHUB_ETF_MAX_HOLDINGS", "10")
)


@dataclass
class ProviderResult:
    available: bool
    provider: str
    records: list[dict[str, Any]]
    message: str


def _get_api_key() -> str:
    return os.getenv(
        "FINNHUB_API_KEY",
        "",
    ).strip()


def _etf_symbols() -> list[str]:
    common_etfs = [
        "SPY",
        "QQQ",
        "IWM",
        "DIA",
        "XLK",
        "XLF",
        "XLE",
        "XLV",
        "SMH",
        "IBB",
        "CIBR",
        "KRE",
    ]

    configured: list[str] = []

    for market_name in ("cash", "stock"):
        for symbol in WATCHLISTS.get(
            market_name,
            [],
        ):
            clean = str(symbol).strip().upper()

            if clean in common_etfs:
                if clean not in configured:
                    configured.append(clean)

    for symbol in common_etfs:
        if symbol not in configured:
            configured.append(symbol)

    return configured[:MAX_ETFS]


def _finnhub_get(
    endpoint: str,
    params: dict[str, Any],
) -> Any:
    api_key = _get_api_key()

    if not api_key:
        raise RuntimeError(
            "FINNHUB_API_KEY is not configured"
        )

    response = requests.get(
        f"{FINNHUB_BASE_URL}/{endpoint}",
        params=params,
        headers={
            "X-Finnhub-Token": api_key,
            "Accept": "application/json",
        },
        timeout=20,
    )

    if response.status_code == 401:
        raise RuntimeError(
            "Finnhub rejected the API key."
        )

    if response.status_code == 403:
        raise RuntimeError(
            "Finnhub denied access to ETF holdings."
        )

    if response.status_code == 429:
        raise RuntimeError(
            "Finnhub request limit reached."
        )

    response.raise_for_status()

    return response.json()


def _normalize_holding(
    etf_symbol: str,
    item: dict[str, Any],
) -> dict[str, Any]:
    return {
        "etf": etf_symbol,
        "symbol": (
            item.get("symbol")
            or item.get("ticker")
        ),
        "name": (
            item.get("name")
            or item.get("holdingName")
        ),
        "weight": (
            item.get("percent")
            or item.get("weight")
        ),
        "shares": item.get("share"),
        "market_value": (
            item.get("value")
            or item.get("marketValue")
        ),
        "provider": "Finnhub",
    }


def fetch() -> ProviderResult:
    guard = provider_guard_state("finnhub_etf_holdings")
    if not provider_available("finnhub_etf_holdings"):
        return ProviderResult(False, "Finnhub ETF Holdings", [], f"ETF holdings temporarily disabled: {guard['reason']} (retry in {guard['cooldown_remaining_seconds']}s).")
    if not _get_api_key():
        return ProviderResult(
            available=False,
            provider="Not configured",
            records=[],
            message=(
                "Add FINNHUB_API_KEY to Railway variables."
            ),
        )

    records: list[dict[str, Any]] = []
    errors: list[str] = []

    for etf_symbol in _etf_symbols():
        try:
            payload = _finnhub_get(
                "etf/holdings",
                {
                    "symbol": etf_symbol,
                },
            )

            if not isinstance(payload, dict):
                continue

            holdings = (
                payload.get("holdings")
                or payload.get("data")
                or []
            )

            if not isinstance(holdings, list):
                continue

            for item in holdings[
                :MAX_HOLDINGS_PER_ETF
            ]:
                if not isinstance(item, dict):
                    continue

                records.append(
                    _normalize_holding(
                        etf_symbol,
                        item,
                    )
                )

        except Exception as exc:
            text = str(exc)
            if "denied access" in text.lower() or "403" in text:
                disable_provider("finnhub_etf_holdings", 86400, text)
                log.warning("Finnhub ETF holdings unavailable for this plan; pausing feature for 24 hours.")
                errors.append(f"provider capability: {text}")
                break
            if "request limit" in text.lower() or "429" in text:
                disable_provider("finnhub_etf_holdings", 3600, text)
                log.warning("Finnhub ETF holdings rate limited; pausing feature for one hour.")
                errors.append(f"rate limit: {text}")
                break
            errors.append(
                f"{etf_symbol}: {exc}"
            )

            log.warning(
                "Finnhub ETF holdings failed for %s: %s",
                etf_symbol,
                exc,
            )

    log.info(
        "Finnhub ETF monitor returned %d records",
        len(records),
    )

    if records:
        return ProviderResult(
            available=True,
            provider="Finnhub ETF Holdings",
            records=records,
            message=(
                f"Loaded {len(records)} holdings records. "
                "These are ETF portfolio holdings, not "
                "daily fund inflows or outflows."
            ),
        )

    if errors:
        return ProviderResult(
            available=False,
            provider="Finnhub",
            records=[],
            message=(
                "No ETF holdings were returned. "
                + " | ".join(errors[:3])
            ),
        )

    return ProviderResult(
        available=True,
        provider="Finnhub",
        records=[],
        message=(
            "Finnhub connected successfully, but no ETF "
            "holdings were returned for this scan."
        ),
    )