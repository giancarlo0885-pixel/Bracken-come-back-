from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Any


PLAN_LIMIT_COOLDOWN_SECONDS = 24 * 60 * 60


CAPABILITY_MATRIX: dict[str, dict[str, bool]] = {
    "Polygon": {
        "live_quotes": True,
        "intraday_history": True,
        "daily_history": True,
        "us_quotes": True,
        "us_history": True,
        "international_history": False,
        "crypto": True,
        "movers": True,
        "exchange_symbol_lists": True,
        "earnings": False,
        "news": True,
        "etf_holdings": False,
        "insider_activity": False,
        "congressional_activity": False,
        "options_activity": False,
    },
    "Finnhub": {
        "live_quotes": True,
        "intraday_history": True,
        "daily_history": True,
        "us_quotes": True,
        "us_history": True,
        "international_history": True,
        "crypto": True,
        "movers": False,
        "exchange_symbol_lists": True,
        "earnings": True,
        "news": True,
        "etf_holdings": True,
        "insider_activity": True,
        "congressional_activity": False,
        "options_activity": False,
    },
    "EODHD": {
        "live_quotes": True,
        "intraday_history": False,
        "daily_history": True,
        "us_quotes": True,
        "us_history": True,
        "international_history": True,
        "crypto": True,
        "movers": True,
        "exchange_symbol_lists": True,
        "earnings": True,
        "news": True,
        "etf_holdings": False,
        "insider_activity": False,
        "congressional_activity": False,
        "options_activity": False,
    },
    "Alpha Vantage": {
        "live_quotes": False,
        "intraday_history": True,
        "daily_history": True,
        "us_quotes": True,
        "us_history": True,
        "international_history": True,
        "crypto": False,
        "movers": True,
        "exchange_symbol_lists": True,
        "earnings": True,
        "news": True,
        "etf_holdings": True,
        "insider_activity": False,
        "congressional_activity": False,
        "options_activity": False,
    },
    "Yahoo Finance": {
        "live_quotes": True,
        "intraday_history": True,
        "daily_history": True,
        "us_quotes": True,
        "us_history": True,
        "international_history": True,
        "crypto": True,
        "movers": False,
        "exchange_symbol_lists": False,
        "earnings": False,
        "news": False,
        "etf_holdings": False,
        "insider_activity": False,
        "congressional_activity": False,
        "options_activity": False,
    },
}


@dataclass
class CapabilityCooldown:
    disabled_until: float
    reason: str
    status_code: int | None = None


_cooldowns: dict[tuple[str, str], CapabilityCooldown] = {}


def _iso_to_epoch(value: Any) -> float:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except Exception:
        return 0.0


def _epoch_to_iso(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _load_persisted_cooldown(provider: str, capability: str) -> CapabilityCooldown | None:
    try:
        from database import row

        record = row(
            """
            SELECT cooldown_until, limitation
            FROM provider_capabilities
            WHERE provider=%s AND capability=%s
            """,
            (provider, capability),
        )
    except Exception:
        return None
    if not record:
        return None
    until = _iso_to_epoch(record.get("cooldown_until"))
    if until <= time.time():
        return None
    return CapabilityCooldown(until, str(record.get("limitation") or "capability cooldown"))


def _persist_capability(provider: str, capability: str, cooldown: CapabilityCooldown | None = None) -> None:
    try:
        from database import connect, utc_now

        supported = capability_supported(provider, capability)
        available = bool(supported and (cooldown is None or cooldown.disabled_until <= time.time()))
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO provider_capabilities
                (provider, capability, supported, available, cooldown_until, limitation, checked_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (provider, capability) DO UPDATE SET
                    supported=EXCLUDED.supported,
                    available=EXCLUDED.available,
                    cooldown_until=EXCLUDED.cooldown_until,
                    limitation=EXCLUDED.limitation,
                    checked_at=EXCLUDED.checked_at
                """,
                (
                    provider,
                    capability,
                    supported,
                    available,
                    _epoch_to_iso(cooldown.disabled_until) if cooldown else None,
                    cooldown.reason if cooldown else "",
                    utc_now(),
                ),
            )
    except Exception:
        return


def capability_supported(provider: str, capability: str) -> bool:
    return bool(CAPABILITY_MATRIX.get(provider, {}).get(capability, False))


def capability_available(provider: str, capability: str) -> bool:
    if (provider, capability) not in _cooldowns:
        persisted = _load_persisted_cooldown(provider, capability)
        if persisted:
            _cooldowns[(provider, capability)] = persisted
    item = _cooldowns.get((provider, capability))
    if item and item.disabled_until > time.time():
        return False
    if item:
        _cooldowns.pop((provider, capability), None)
    return capability_supported(provider, capability)


def disable_capability(provider: str, capability: str, reason: str, *, status_code: int | None = None, seconds: int = PLAN_LIMIT_COOLDOWN_SECONDS) -> None:
    cooldown = CapabilityCooldown(
        disabled_until=time.time() + max(60, int(seconds)),
        reason=str(reason),
        status_code=status_code,
    )
    _cooldowns[(provider, capability)] = cooldown
    _persist_capability(provider, capability, cooldown)


def classify_plan_limited_status(status_code: int | None, message: str = "") -> bool:
    text = str(message or "").lower()
    return status_code in {402, 403} or any(
        phrase in text
        for phrase in (
            "subscription",
            "premium",
            "not entitled",
            "plan limit",
            "plan-limited",
            "plan not supported",
            "not available under your current plan",
            "available on your current plan",
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
