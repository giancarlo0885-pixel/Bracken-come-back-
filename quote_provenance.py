"""Keep provider verification distinct from paper-reference eligibility."""
from __future__ import annotations

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
    if paper_context:
        return True
    return provider_verified(payload)
