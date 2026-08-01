from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from config import PRICE_CONSENSUS_MAX_DIFF_PCT
from market_sessions import parse_utc, quote_is_fresh
from provider_router import normalize_symbol


@dataclass
class QuoteVerification:
    symbol: str
    primary_price: float
    secondary_price: float | None
    difference_pct: float | None
    primary_provider: str
    secondary_provider: str | None
    primary_timestamp: str
    secondary_timestamp: str | None
    consensus_status: str
    reason: str


def _price(payload: dict[str, Any]) -> float:
    try:
        value = float(payload.get("price"))
        return value if value > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _identity_ok(symbol: str, quote: dict[str, Any]) -> bool:
    requested = normalize_symbol(symbol)
    return (
        normalize_symbol(quote.get("symbol") or requested) == requested
        and normalize_symbol(quote.get("requested_symbol")) == requested
        and normalize_symbol(quote.get("provider_symbol")) == requested
        and quote.get("quote_verified") is True
    )


def verify_price_consensus(
    symbol: str,
    primary: dict[str, Any],
    secondary: dict[str, Any] | None = None,
    *,
    market: str = "cash",
    require_secondary: bool = False,
    tolerance_pct: float = PRICE_CONSENSUS_MAX_DIFF_PCT,
    now: datetime | None = None,
) -> QuoteVerification:
    requested = normalize_symbol(symbol)
    primary_price = _price(primary)
    primary_time = str(primary.get("quote_timestamp") or primary.get("timestamp") or "")
    if not _identity_ok(requested, primary) or primary_price <= 0:
        return QuoteVerification(requested, primary_price, None, None, str(primary.get("provider") or ""), None, primary_time, None, "rejected", "primary quote identity failed")
    if parse_utc(primary_time) is None or not quote_is_fresh(primary_time, str(primary.get("interval") or "1d"), now, symbol=requested):
        return QuoteVerification(requested, primary_price, None, None, str(primary.get("provider") or ""), None, primary_time, None, "rejected", "primary quote is stale")
    if secondary is None:
        status = "single_provider" if not require_secondary else "rejected"
        reason = "secondary provider unavailable" if require_secondary else "single verified provider accepted"
        return QuoteVerification(requested, primary_price, None, None, str(primary.get("provider") or ""), None, primary_time, None, status, reason)

    secondary_price = _price(secondary)
    secondary_time = str(secondary.get("quote_timestamp") or secondary.get("timestamp") or "")
    if not _identity_ok(requested, secondary) or secondary_price <= 0:
        return QuoteVerification(requested, primary_price, secondary_price, None, str(primary.get("provider") or ""), str(secondary.get("provider") or ""), primary_time, secondary_time, "rejected", "secondary quote identity failed")
    if parse_utc(secondary_time) is None or not quote_is_fresh(secondary_time, str(secondary.get("interval") or "1d"), now, symbol=requested):
        return QuoteVerification(requested, primary_price, secondary_price, None, str(primary.get("provider") or ""), str(secondary.get("provider") or ""), primary_time, secondary_time, "rejected", "secondary quote is stale")
    if str(primary.get("currency") or secondary.get("currency") or "").upper() and str(primary.get("currency") or "").upper() != str(secondary.get("currency") or "").upper():
        return QuoteVerification(requested, primary_price, secondary_price, None, str(primary.get("provider") or ""), str(secondary.get("provider") or ""), primary_time, secondary_time, "rejected", "currency mismatch")
    diff = abs(primary_price - secondary_price) / primary_price * 100.0
    if diff > tolerance_pct:
        return QuoteVerification(requested, primary_price, secondary_price, diff, str(primary.get("provider") or ""), str(secondary.get("provider") or ""), primary_time, secondary_time, "rejected", "provider prices differ beyond tolerance")
    return QuoteVerification(requested, primary_price, secondary_price, diff, str(primary.get("provider") or ""), str(secondary.get("provider") or ""), primary_time, secondary_time, "verified", "two-provider consensus verified")
