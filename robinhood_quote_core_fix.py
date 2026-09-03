from __future__ import annotations

from decimal import Decimal
from typing import Any

import robinhood_crypto_api as rh


def _first_present(quote: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = quote.get(key)
        if value not in (None, ""):
            return value
    return None


def _normalized_book(quote: dict[str, Any]) -> dict[str, Decimal] | None:
    """Parse every documented Robinhood best-bid/ask field shape.

    Values are never synthesized: both sides must be supplied by Robinhood,
    positive, finite Decimals, and ask must be >= bid.
    """
    if not isinstance(quote, dict):
        return None
    try:
        bid_raw = _first_present(
            quote,
            "bid_price",
            "bid",
            "bid_inclusive_of_sell_spread",
        )
        ask_raw = _first_present(
            quote,
            "ask_price",
            "ask",
            "ask_inclusive_of_buy_spread",
        )
        if bid_raw is None or ask_raw is None:
            return None
        bid = Decimal(str(bid_raw))
        ask = Decimal(str(ask_raw))
    except Exception:
        return None
    if not bid.is_finite() or not ask.is_finite() or bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / Decimal("2")
    if mid <= 0:
        return None
    spread_pct = ((ask - bid) / mid) * Decimal("100")
    return {"bid": bid, "ask": ask, "mid": mid, "spread_pct": spread_pct}


def install_robinhood_quote_core_fix() -> bool:
    """Install one canonical broker-book parser for all read-only execution paths."""
    if getattr(rh, "_oracle_quote_core_fix_installed", False):
        return False
    rh.best_bid_ask = _normalized_book
    rh._oracle_quote_core_fix_installed = True
    return True
