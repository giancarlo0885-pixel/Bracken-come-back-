from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any


PLAN_LIMIT_COOLDOWN_SECONDS = 24 * 60 * 60


CAPABILITY_MATRIX: dict[str, dict[str, bool]] = {
    "Polygon": {
        "us_quotes": True,
        "us_history": True,
        "international_history": False,
        "crypto": True,
        "movers": True,
        "exchange_symbol_lists": True,
        "earnings": False,
        "news": True,
        "etf_holdings": False,
    },
    "Finnhub": {
        "us_quotes": True,
        "us_history": True,
        "international_history": True,
        "crypto": True,
        "movers": False,
        "exchange_symbol_lists": True,
        "earnings": True,
        "news": True,
        "etf_holdings": True,
    },
    "EODHD": {
        "us_quotes": True,
        "us_history": True,
        "international_history": True,
        "crypto": True,
        "movers": True,
        "exchange_symbol_lists": True,
        "earnings": True,
        "news": True,
        "etf_holdings": False,
    },
    "Alpha Vantage": {
        "us_quotes": True,
        "us_history": True,
        "international_history": True,
        "crypto": True,
        "movers": True,
        "exchange_symbol_lists": False,
        "earnings": True,
        "news": True,
        "etf_holdings": False,
    },
    "Yahoo Finance": {
        "us_quotes": True,
        "us_history": True,
        "international_history": True,
        "crypto": True,
        "movers": False,
        "exchange_symbol_lists": False,
        "earnings": False,
        "news": False,
        "etf_holdings": False,
    },
}


@dataclass
class CapabilityCooldown:
    disabled_until: float
    reason: str
    status_code: int | None = None


_cooldowns: dict[tuple[str, str], CapabilityCooldown] = {}


def capability_supported(provider: str, capability: str) -> bool:
    return bool(CAPABILITY_MATRIX.get(provider, {}).get(capability, False))


def capability_available(provider: str, capability: str) -> bool:
    item = _cooldowns.get((provider, capability))
    if item and item.disabled_until > time.time():
        return False
    if item:
        _cooldowns.pop((provider, capability), None)
    return capability_supported(provider, capability)


def disable_capability(provider: str, capability: str, reason: str, *, status_code: int | None = None, seconds: int = PLAN_LIMIT_COOLDOWN_SECONDS) -> None:
    _cooldowns[(provider, capability)] = CapabilityCooldown(
        disabled_until=time.time() + max(60, int(seconds)),
        reason=str(reason),
        status_code=status_code,
    )


def classify_plan_limited_status(status_code: int | None, message: str = "") -> bool:
    text = str(message or "").lower()
    return status_code in {402, 403} or any(
        phrase in text
        for phrase in (
            "plan",
            "subscription",
            "premium",
            "not entitled",
            "not available under your current plan",
        )
    )


def diagnostics() -> list[dict[str, Any]]:
    now = time.time()
    records: list[dict[str, Any]] = []
    for provider, capabilities in CAPABILITY_MATRIX.items():
        for capability, supported in sorted(capabilities.items()):
            cooldown = _cooldowns.get((provider, capability))
            remaining = max(0, int((cooldown.disabled_until - now) if cooldown else 0))
            records.append(
                {
                    "provider": provider,
                    "capability": capability,
                    "supported": supported,
                    "available": bool(supported and remaining == 0),
                    "cooldown_remaining_seconds": remaining,
                    "limitation": cooldown.reason if cooldown and remaining else "",
                    "status_code": cooldown.status_code if cooldown else None,
                }
            )
    return records
