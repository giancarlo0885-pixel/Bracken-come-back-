from __future__ import annotations

import os
import uuid
from typing import Any


_INSTALLED = False


def _truthy(value: Any) -> bool:
    return value is True


def _small_account_position_cap() -> int:
    try:
        value = int(os.getenv("SMALL_ACCOUNT_MAX_OPEN_POSITIONS", "10"))
    except (TypeError, ValueError):
        value = 10
    return max(1, min(20, value))


def _install_small_account_position_cap(oracle_bot_module: Any) -> int | None:
    """Cap paper small-account concurrency without weakening any entry gate."""
    execution_mode = str(os.getenv("EXECUTION_MODE", "paper") or "paper").strip().lower()
    profile = str(os.getenv("PAPER_BROKER_PROFILE", "small-account-paper") or "").strip().lower()
    if execution_mode != "paper" or profile != "small-account-paper":
        return None

    cap = _small_account_position_cap()
    configured_base = max(1, int(getattr(oracle_bot_module, "DEFAULT_MAX_OPEN_POSITIONS", cap)))
    oracle_bot_module.DEFAULT_MAX_OPEN_POSITIONS = min(configured_base, cap)
    oracle_bot_module.EXTRA_OPEN_POSITIONS = 0
    return min(configured_base, cap)


def _verification_metadata(route: dict[str, Any], payload: dict[str, Any]) -> None:
    provider_verified = _truthy(route.get("provider_quote_verified"))
    paper_reference_verified = _truthy(route.get("paper_reference_verified"))
    quote_eligible = _truthy(payload.get("quote_verified"))

    payload["provider_quote_verified"] = provider_verified
    payload["paper_reference_verified"] = paper_reference_verified
    payload["execution_quote_eligible"] = quote_eligible
    payload["verified"] = provider_verified
    payload["verification_basis"] = str(route.get("verification_basis") or "unverified")
    if provider_verified:
        payload["verification_kind"] = "provider"
    elif paper_reference_verified:
        payload["verification_kind"] = "paper_reference"
    else:
        payload["verification_kind"] = "unverified"

    correlation_id = str(
        payload.get("correlation_id")
        or payload.get("decision_correlation_id")
        or route.get("correlation_id")
        or route.get("decision_correlation_id")
        or uuid.uuid4()
    ).strip()
    payload["correlation_id"] = correlation_id
    payload["decision_correlation_id"] = correlation_id


def install_runtime_integrity_patch(market_worker_module: Any) -> None:
    """Install fail-closed runtime corrections for paper workers.

    `quote_verified` remains the existing internal eligibility flag so paper
    behavior is not silently changed. Human-facing `verified` and logs now mean
    provider verification only, while Yahoo paper-reference eligibility is
    exposed separately.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    import oracle_bot

    effective_cap = _install_small_account_position_cap(oracle_bot)
    if effective_cap is not None:
        market_worker_module.log.info(
            "RUNTIME INTEGRITY | paper_small_account_max_positions=%s | extra_open_positions=0",
            effective_cap,
        )

    original_quote_payload = market_worker_module._quote_payload_from_history

    def quote_payload_from_history(
        symbol: str,
        history: Any,
        price: Any = None,
        *,
        scan_type: str = "",
    ) -> dict[str, Any]:
        payload = original_quote_payload(symbol, history, price, scan_type=scan_type)
        route = dict(getattr(history, "attrs", {}).get("provider_route", {}) or {})
        _verification_metadata(route, payload)
        return payload

    def execution_quote_payload_from_history(
        symbol: str,
        history: Any,
        price: Any = None,
        *,
        scan_type: str = "",
    ) -> dict[str, Any] | None:
        payload = quote_payload_from_history(symbol, history, price, scan_type=scan_type)
        execution_price = market_worker_module._finite_positive(payload.get("price"))
        if execution_price is None:
            market_worker_module._v39_log_rejection(
                symbol,
                "ZERO_PRICE",
                {"scan_type": scan_type, "provider": payload.get("provider")},
            )
            return None
        payload["price"] = execution_price
        if payload.get("quote_timestamp") in (None, ""):
            market_worker_module._v39_log_rejection(
                symbol,
                "QUOTE_STALE",
                {"scan_type": scan_type, "provider": payload.get("provider")},
            )
            return None

        handoff_eligible = (
            payload.get("quote_verified") is True
            and payload.get("stale") is False
            and execution_price > 0
        )
        handoff_log = market_worker_module.log.info if handoff_eligible else market_worker_module.log.debug
        handoff_log(
            "EXECUTION_QUOTE_HANDOFF | symbol=%s | market=%s | price=%s | bid=%s | ask=%s | "
            "timestamp=%s | provider=%s | quote_eligible=%s | provider_verified=%s | "
            "paper_reference_verified=%s | verification_kind=%s | stale=%s | spread_pct=%s | "
            "capability=%s | correlation_id=%s",
            str(symbol or "").upper(),
            payload.get("market"),
            payload.get("price"),
            payload.get("bid"),
            payload.get("ask"),
            payload.get("quote_timestamp"),
            payload.get("provider"),
            payload.get("quote_verified"),
            payload.get("provider_quote_verified"),
            payload.get("paper_reference_verified"),
            payload.get("verification_kind"),
            payload.get("stale"),
            payload.get("spread_pct"),
            payload.get("source_capability"),
            payload.get("correlation_id"),
        )
        return payload

    market_worker_module._quote_payload_from_history = quote_payload_from_history
    market_worker_module._execution_quote_payload_from_history = execution_quote_payload_from_history
    _INSTALLED = True
