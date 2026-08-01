from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as clock_time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from config import (
    CRYPTO_DEEP_SCAN_SECONDS,
    CRYPTO_FAST_SCAN_SECONDS,
    CRYPTO_PULSE_SECONDS,
    STOCK_CLOSED_SCAN_SECONDS,
    STOCK_DEEP_SCAN_SECONDS,
    STOCK_FAST_SCAN_SECONDS,
    STOCK_PULSE_SECONDS,
)

NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class RuntimeCadence:
    pulse_seconds: int
    fast_scan_seconds: int
    deep_scan_seconds: int
    session_label: str


def utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def market_session(market: str, now: datetime | None = None) -> str:
    """Return a simple session label used for scan cadence and the dashboard."""
    market = str(market or "").strip().lower()
    if market == "crypto":
        return "24/7"

    current = now or utc_now_dt()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    eastern = current.astimezone(NEW_YORK)

    if eastern.weekday() >= 5:
        return "closed"
    local_time = eastern.time()
    if clock_time(4, 0) <= local_time < clock_time(9, 30):
        return "pre-market"
    if clock_time(9, 30) <= local_time < clock_time(16, 0):
        return "regular"
    if clock_time(16, 0) <= local_time < clock_time(20, 0):
        return "after-hours"
    return "closed"


def cadence_for(market: str, now: datetime | None = None) -> RuntimeCadence:
    market = str(market or "").strip().lower()
    session = market_session(market, now)
    if market == "crypto":
        return RuntimeCadence(
            CRYPTO_PULSE_SECONDS,
            CRYPTO_FAST_SCAN_SECONDS,
            CRYPTO_DEEP_SCAN_SECONDS,
            session,
        )
    deep = STOCK_DEEP_SCAN_SECONDS if session in {"pre-market", "regular", "after-hours"} else STOCK_CLOSED_SCAN_SECONDS
    return RuntimeCadence(
        STOCK_PULSE_SECONDS,
        STOCK_FAST_SCAN_SECONDS,
        deep,
        session,
    )


def seconds_until(when_monotonic: float, now_monotonic: float) -> int:
    return max(0, int(round(when_monotonic - now_monotonic)))


def status_age_seconds(value: Any, now: datetime | None = None) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        current = now or utc_now_dt()
        return max(0.0, (current - parsed.astimezone(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None
