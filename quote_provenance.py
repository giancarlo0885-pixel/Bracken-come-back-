"""Keep provider verification distinct from paper-reference eligibility."""
from __future__ import annotations

import math
from typing import Any


def is_paper_reference(payload: dict[str, Any]) -> bool:
    return bool(
        payload.get("paper_reference_verified") is True
        or str(payload.get("provider") or "").strip().lower() == "yahoo finance"
    )


def provider_verified(payload: dict[str, Any]) -> bool:
    """Explicit negative provenance overrides the legacy eligibility alias."""
    if is_paper_reference(payload):
        return False
    if "provider_quote_verified" in payload:
        return payload.get("provider_quote_verified") is True
    return payload.get("quote_verified") is True


def reference_allowed_in_mode(payload: dict[str, Any], mode: str, paper_enabled: bool) -> bool:
    """A paper mark cannot cross into a broker/live execution context."""
    paper_context = str(mode or "").strip().lower() == "paper" and paper_enabled is True
    paper_eligible = (payload.get("paper_reference_verified") is True
                      if "paper_reference_verified" in payload else is_paper_reference(payload))
    return provider_verified(payload) or (paper_context and paper_eligible)


def quote_quality_rejection_reason(payload: dict[str, Any]) -> str | None:
    """Honor explicit quality vetoes without inventing missing book sides."""
    if isinstance(payload.get("price"), bool):
        return "quote price is invalid"
    if payload.get("stale") is True:
        return "quote explicitly marked stale"
    if payload.get("execution_quote_eligible") is False:
        return "quote explicitly ineligible for execution"
    bid_value, ask_value = payload.get("bid"), payload.get("ask")
    if bid_value is None and ask_value is None:
        # A paper-reference mark may legitimately have no measured bid/ask.
        return None
    if isinstance(bid_value, bool) or isinstance(ask_value, bool):
        return "quote bid/ask book is invalid"
    try:
        bid, ask = float(bid_value), float(ask_value)
    except (TypeError, ValueError, OverflowError):
        return "quote bid/ask book is invalid"
    if not all(math.isfinite(value) and value > 0 for value in (bid, ask)):
        return "quote bid/ask book is invalid"
    if ask < bid:
        return "quote bid/ask book is crossed"
    return None
