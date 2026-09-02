from __future__ import annotations

import os
import uuid
from typing import Any


_INSTALLED = False
CORE_REBALANCE_BUY_INTENT = "CORE_REBALANCE_BUY"
CORE_REBALANCE_CANDIDATE_INTENT = "CORE_REBALANCE_CANDIDATE"
CORE_REBALANCE_STRATEGIC_CANDIDATE_INTENT = "CORE_REBALANCE_STRATEGIC_CANDIDATE"


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


def _signal_value(signal: Any, name: str, default: Any = None) -> Any:
    if isinstance(signal, dict):
        return signal.get(name, default)
    return getattr(signal, name, default)


def _set_signal_value(signal: Any, name: str, value: Any) -> None:
    if isinstance(signal, dict):
        signal[name] = value
    else:
        setattr(signal, name, value)


def _core_rebalance_intent(signal: Any) -> str:
    """Return an explicit rebalance intent without inferring intent from HOLD alone."""
    for field in ("rebalance_intent", "execution_intent", "portfolio_intent", "v39_intent"):
        value = str(_signal_value(signal, field, "") or "").strip().upper()
        if value:
            return value
    payload = _signal_value(signal, "payload", None)
    if isinstance(payload, dict):
        for field in ("rebalance_intent", "execution_intent", "portfolio_intent", "v39_intent"):
            value = str(payload.get(field) or "").strip().upper()
            if value:
                return value
    return ""


def _normalize_core_rebalance_action(signal: Any) -> Any:
    """Promote only an explicitly authorized core-rebalance HOLD into an entry candidate.

    A normal HOLD remains a non-entry. CORE_REBALANCE_BUY only changes the action
    to ACCUMULATE so the existing V39 optimizer, forecast, quote, risk, sizing,
    and execution gates can evaluate it; it does not approve or size a trade.
    """
    if signal is None:
        return signal
    action = str(_signal_value(signal, "action", "HOLD") or "HOLD").strip().upper()
    if action != "HOLD" or _core_rebalance_intent(signal) != CORE_REBALANCE_BUY_INTENT:
        return signal
    _set_signal_value(signal, "v39_original_action", "HOLD")
    _set_signal_value(signal, "v39_normalization_reason", CORE_REBALANCE_BUY_INTENT)
    _set_signal_value(signal, "action", "ACCUMULATE")
    return signal


def _numeric(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number else default


def _core_rebalance_candidate_allowed(
    signal: Any,
    *,
    market: str,
    deployment_gap: float,
    score_threshold: float,
    confidence_threshold: float,
) -> bool:
    """Identify a HOLD that may be ranked for core allocation, not executed.

    This is intentionally narrower than converting HOLD to BUY: it only labels a
    crypto HOLD as a portfolio candidate while capital is under-deployed and the
    signal still meets the worker's normal quality thresholds. Actual buy intent
    is emitted only after V39 returns a positive allocation for the same symbol.
    """
    if str(market or "").strip().lower() != "crypto":
        return False
    if deployment_gap <= 0:
        return False
    if str(_signal_value(signal, "action", "HOLD") or "HOLD").strip().upper() != "HOLD":
        return False
    existing_intent = _core_rebalance_intent(signal)
    if existing_intent not in {CORE_REBALANCE_CANDIDATE_INTENT, CORE_REBALANCE_STRATEGIC_CANDIDATE_INTENT}:
        return False
    score = _numeric(_signal_value(signal, "score", 0.0))
    confidence = _numeric(_signal_value(signal, "confidence", 0.0))
    return score >= score_threshold and confidence >= confidence_threshold


def _install_core_rebalance_producer(market_worker_module: Any) -> None:
    """Bridge underweight crypto HOLDs into V39 without bypassing safety gates.

    Candidate HOLDs are temporarily presented to the existing V39 opportunity
    evaluator as ACCUMULATE *only for capital qualification*. Their tactical
    action is restored immediately. This preserves the evaluator's existing
    quote identity/freshness, tradeability, liquidity, spread, risk, signal-id,
    and forecast-id requirements. V39 then applies hard risk, liquidity capacity,
    reserve, position concentration, sector concentration, buying-power, margin,
    and downstream execution checks. Only a positive optimizer allocation emits
    CORE_REBALANCE_BUY and normalizes the signal for the existing paper-buy path.
    """
    from global_adaptive_engine import capital_engine_state

    original_opportunity = market_worker_module._v39_signal_opportunity
    if getattr(original_opportunity, "_oracle_core_rebalance_aware", False):
        return

    def rebalance_aware_opportunity(
        market: str,
        signal: Any,
        prices: dict[str, Any],
        ranked_by_symbol: dict[str, dict[str, Any]],
        scan_type: str,
    ) -> dict[str, Any]:
        candidate = _core_rebalance_intent(signal) in {
            CORE_REBALANCE_CANDIDATE_INTENT,
            CORE_REBALANCE_STRATEGIC_CANDIDATE_INTENT,
        }
        if not candidate:
            return original_opportunity(market, signal, prices, ranked_by_symbol, scan_type)

        original_action = str(_signal_value(signal, "action", "HOLD") or "HOLD").strip().upper()
        _set_signal_value(signal, "action", "ACCUMULATE")
        try:
            opportunity = original_opportunity(market, signal, prices, ranked_by_symbol, scan_type)
        finally:
            _set_signal_value(signal, "action", original_action)

        stages = [stage for stage in list(opportunity.get("stages") or []) if stage != "buy_signal"]
        if "core_rebalance_candidate" not in stages:
            stages.append("core_rebalance_candidate")
        opportunity["stages"] = stages
        opportunity["core_rebalance_candidate"] = True
        opportunity["portfolio_intent"] = CORE_REBALANCE_CANDIDATE_INTENT
        opportunity["tactical_action"] = original_action
        if opportunity.get("trend_score") in (None, "") and _numeric(_signal_value(signal, "score", 0.0)) > 0:
            opportunity["trend_score"] = _numeric(_signal_value(signal, "score", 0.0))
        return opportunity

    rebalance_aware_opportunity._oracle_core_rebalance_aware = True
    market_worker_module._v39_signal_opportunity = rebalance_aware_opportunity

    original_prioritize = market_worker_module._v39_prioritize_signals

    def rebalance_aware_prioritize(
        market: str,
        signals: list[Any],
        prices: dict[str, Any],
        ranked: list[dict[str, Any]],
        scan_type: str,
    ) -> list[Any]:
        if str(market or "").strip().lower() == "crypto" and signals:
            try:
                portfolio, positions = market_worker_module._v39_position_rows(market)
                state = capital_engine_state(portfolio, positions, [], "crypto")
                deployment_gap = max(0.0, _numeric(state.get("deployment_gap")))
            except Exception as exc:
                market_worker_module.log.debug(
                    "CORE_REBALANCE candidate scan skipped | market=%s | error=%s",
                    market,
                    exc,
                )
                deployment_gap = 0.0

            score_threshold = _numeric(getattr(market_worker_module, "HIGH_SCORE_THRESHOLD", 0.0))
            confidence_threshold = _numeric(getattr(market_worker_module, "HIGH_CONFIDENCE_THRESHOLD", 0.0))
            for signal in signals:
                if _core_rebalance_candidate_allowed(
                    signal,
                    market=market,
                    deployment_gap=deployment_gap,
                    score_threshold=score_threshold,
                    confidence_threshold=confidence_threshold,
                ):
                    _set_signal_value(signal, "portfolio_intent", CORE_REBALANCE_CANDIDATE_INTENT)
                    _set_signal_value(signal, "v39_rebalance_deployment_gap", deployment_gap)

        ordered = original_prioritize(market, signals, prices, ranked, scan_type)

        for signal in ordered or []:
            if _core_rebalance_intent(signal) != CORE_REBALANCE_CANDIDATE_INTENT:
                continue
            approved_amount = market_worker_module._finite_positive(
                _signal_value(signal, "v39_optimizer_approved_amount", None)
            )
            allocation = _signal_value(signal, "v39_optimizer_allocation", {}) or {}
            symbol = str(_signal_value(signal, "symbol", "") or "").strip().upper()
            allocation_symbol = str(allocation.get("symbol") or "").strip().upper()
            if approved_amount is None or not symbol or allocation_symbol != symbol:
                continue

            _set_signal_value(signal, "rebalance_intent", CORE_REBALANCE_BUY_INTENT)
            _set_signal_value(signal, "portfolio_intent", CORE_REBALANCE_BUY_INTENT)
            _set_signal_value(signal, "v39_rebalance_approved_amount", approved_amount)
            _normalize_core_rebalance_action(signal)
            market_worker_module.log.info(
                "CORE_REBALANCE_BUY | market=%s | symbol=%s | approved_amount=%s | action=%s",
                market,
                symbol,
                approved_amount,
                _signal_value(signal, "action", ""),
            )
        return ordered

    rebalance_aware_prioritize._oracle_core_rebalance_aware = True
    market_worker_module._v39_prioritize_signals = rebalance_aware_prioritize


def _verification_metadata(route: dict[str, Any], payload: dict[str, Any]) -> None:
    """Expose verification types without changing legacy execution eligibility.

    `quote_verified`/`verified` are retained as the historical generic eligibility
    aliases because existing execution code relies on them. Explicit fields now
    distinguish a provider-verified quote from a paper-reference fallback.
    """
    quote_eligible = _truthy(payload.get("quote_verified"))
    paper_reference_verified = _truthy(route.get("paper_reference_verified"))

    if "provider_quote_verified" in route:
        provider_verified = _truthy(route.get("provider_quote_verified"))
    else:
        # Backward-compatible provider/unit routes historically supplied only
        # quote_verified. Never use this fallback for a marked paper reference.
        provider_verified = bool(quote_eligible and not paper_reference_verified)

    payload["provider_quote_verified"] = provider_verified
    payload["paper_reference_verified"] = paper_reference_verified
    payload["execution_quote_eligible"] = quote_eligible
    payload["verified"] = quote_eligible
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


def _install_fast_quarantine_filter(market_worker_module: Any) -> None:
    """Prevent quarantined non-held symbols from leaking back into fast scans.

    The deep scanner already filters the persistent invalid-symbol quarantine and
    ranked fast candidates are filtered at query time. The rotating watchlist
    slice historically bypassed that filter, allowing a known-bad provider symbol
    to be retried immediately. Held positions are deliberately retained so price
    monitoring and risk exits are never disabled by a data-source quarantine.
    """
    original = market_worker_module._fast_candidate_batch
    if getattr(original, "_oracle_quarantine_aware", False):
        return

    def quarantine_aware(market: str) -> list[tuple[str, str]]:
        candidates = list(original(market) or [])
        quarantined = {
            str(symbol or "").upper().strip()
            for symbol in (market_worker_module._active_quarantined_symbols() or set())
            if str(symbol or "").strip()
        }
        if not quarantined:
            return candidates
        held = {
            str(symbol or "").upper().strip()
            for symbol in (market_worker_module._held_symbols(market) or set())
            if str(symbol or "").strip()
        }
        return [
            (symbol, name)
            for symbol, name in candidates
            if str(symbol or "").upper().strip() in held
            or str(symbol or "").upper().strip() not in quarantined
        ]

    quarantine_aware._oracle_quarantine_aware = True
    market_worker_module._fast_candidate_batch = quarantine_aware


def install_runtime_integrity_patch(market_worker_module: Any) -> None:
    """Install fail-closed runtime corrections for paper workers.

    Legacy `quote_verified`/`verified` keep their existing generic execution-
    eligibility meaning. New explicit metadata and log fields make provider
    verification and Yahoo paper-reference verification unambiguous. Core crypto
    rebalancing now has an explicit producer: under-deployed HOLDs may enter V39
    as candidates, but only optimizer-approved allocations emit CORE_REBALANCE_BUY
    and reach the existing paper execution path. Persistent symbol quarantine is
    also enforced for non-held fast-scan candidates.
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

    _install_fast_quarantine_filter(market_worker_module)

    original_normalize_starter_action = market_worker_module._normalize_starter_action

    def normalize_starter_action(signal: Any) -> Any:
        normalized = original_normalize_starter_action(signal)
        return _normalize_core_rebalance_action(normalized)

    market_worker_module._normalize_starter_action = normalize_starter_action
    _install_core_rebalance_producer(market_worker_module)

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
            "timestamp=%s | provider=%s | verified=%s | quote_eligible=%s | provider_verified=%s | "
            "paper_reference_verified=%s | verification_kind=%s | stale=%s | spread_pct=%s | "
            "capability=%s | correlation_id=%s",
            str(symbol or "").upper(),
            payload.get("market"),
            payload.get("price"),
            payload.get("bid"),
            payload.get("ask"),
            payload.get("quote_timestamp"),
            payload.get("provider"),
            payload.get("verified"),
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
