from __future__ import annotations

from typing import Any

from crypto_opportunity_engine import crypto_core_rebalance_plan
import runtime_integrity_patch as patch


def _row_symbol(row: dict[str, Any]) -> str:
    return str(row.get("Asset") or row.get("symbol") or "").upper().strip()


def _promotion_rejection_reason(signal: Any) -> str:
    intent = patch._core_rebalance_intent(signal)
    raw_amount = patch._signal_value(signal, "v39_optimizer_approved_amount", None)
    approved_amount = patch._numeric(raw_amount, default=0.0)
    allocation = patch._signal_value(signal, "v39_optimizer_allocation", {}) or {}
    symbol = str(patch._signal_value(signal, "symbol", "") or "").upper().strip()
    allocation_symbol = str(allocation.get("symbol") or "").upper().strip()

    if intent == patch.CORE_REBALANCE_BUY_INTENT:
        return "approved"
    if intent not in {
        patch.CORE_REBALANCE_CANDIDATE_INTENT,
        patch.CORE_REBALANCE_STRATEGIC_CANDIDATE_INTENT,
    }:
        return f"intent_changed:{intent or 'missing'}"
    if approved_amount <= 0:
        return "optimizer_amount_missing_or_nonpositive"
    if not symbol:
        return "signal_symbol_missing"
    if allocation_symbol != symbol:
        return f"allocation_symbol_mismatch:{allocation_symbol or 'missing'}"
    return "promotion_not_emitted_after_valid_allocation"


def _log_promotion_decision(worker: Any, signal: Any) -> None:
    source = str(patch._signal_value(signal, "core_rebalance_source", "") or "")
    if source != "configured_core_allocation_gap":
        return

    symbol = str(patch._signal_value(signal, "symbol", "") or "").upper().strip()
    intent = patch._core_rebalance_intent(signal)
    raw_amount = patch._signal_value(signal, "v39_optimizer_approved_amount", None)
    approved_amount = patch._numeric(raw_amount, default=0.0)
    allocation = patch._signal_value(signal, "v39_optimizer_allocation", {}) or {}
    allocation_symbol = str(allocation.get("symbol") or "").upper().strip()
    reason = _promotion_rejection_reason(signal)
    approved = intent == patch.CORE_REBALANCE_BUY_INTENT

    worker.log.info(
        "CORE_REBALANCE_PROMOTION_DECISION | symbol=%s | approved=%s | intent=%s | "
        "approved_amount_raw=%s | approved_amount=%.2f | allocation_symbol=%s | "
        "action=%s | reason=%s",
        symbol,
        approved,
        intent or "missing",
        raw_amount,
        approved_amount,
        allocation_symbol or "missing",
        patch._signal_value(signal, "action", ""),
        reason,
    )


def install_strategic_core_rebalance_producer(worker: Any) -> None:
    """Authorize V39 core-rebalance candidates from configured portfolio deficits.

    The configured core allocator is the strategic producer. A tactical HOLD is
    not itself an authorization. Only a symbol that appears in
    ``crypto_core_rebalance_plan`` with a positive deficit and a current verified
    quote receives ``CORE_REBALANCE_CANDIDATE`` metadata. V39 still owns final
    capital approval and no signal becomes CORE_REBALANCE_BUY here.
    """
    original = worker._v39_prioritize_signals
    if getattr(original, "_oracle_strategic_core_producer", False):
        return

    def strategic_prioritize(
        market: str,
        signals: list[Any],
        prices: dict[str, Any],
        ranked: list[dict[str, Any]],
        scan_type: str,
    ) -> list[Any]:
        if str(market or "").strip().lower() == "crypto" and signals and prices:
            try:
                portfolio, positions = worker._v39_position_rows(market)
                plan_rows = crypto_core_rebalance_plan(prices, portfolio, positions)
            except Exception as exc:
                worker.log.info(
                    "CORE_REBALANCE_STRATEGIC_PLAN_BLOCKED | reason=%s",
                    exc.__class__.__name__,
                )
                plan_rows = []

            plan_by_symbol = {
                _row_symbol(row): row
                for row in plan_rows
                if _row_symbol(row) and patch._numeric(row.get("Amount")) > 0
            }
            for signal in signals:
                symbol = str(patch._signal_value(signal, "symbol", "") or "").upper().strip()
                row = plan_by_symbol.get(symbol)
                if row is None:
                    continue
                # Never reinterpret SELL/exit intent as a strategic entry.
                if str(patch._signal_value(signal, "action", "HOLD") or "HOLD").upper() != "HOLD":
                    continue
                existing = patch._core_rebalance_intent(signal)
                if existing not in {"", patch.CORE_REBALANCE_CANDIDATE_INTENT}:
                    continue

                patch._set_signal_value(signal, "portfolio_intent", patch.CORE_REBALANCE_CANDIDATE_INTENT)
                patch._set_signal_value(signal, "core_rebalance_source", "configured_core_allocation_gap")
                patch._set_signal_value(signal, "core_bucket", "Core")
                patch._set_signal_value(signal, "core_target_amount", patch._numeric(row.get("Amount")))
                patch._set_signal_value(signal, "core_target_weight", row.get("Target Weight"))
                patch._set_signal_value(signal, "core_current_value", patch._numeric(row.get("Current Core Value")))
                patch._set_signal_value(signal, "core_plan_reason", row.get("Reason"))
                worker.log.info(
                    "CORE_REBALANCE_STRATEGIC_CANDIDATE | symbol=%s | target_amount=%.2f | target_weight=%s | "
                    "current_core_value=%.2f | action=%s",
                    symbol,
                    patch._numeric(row.get("Amount")),
                    row.get("Target Weight"),
                    patch._numeric(row.get("Current Core Value")),
                    patch._signal_value(signal, "action", ""),
                )

        ordered = original(market, signals, prices, ranked, scan_type)
        if str(market or "").strip().lower() == "crypto":
            for signal in ordered or []:
                _log_promotion_decision(worker, signal)
        return ordered

    strategic_prioritize._oracle_strategic_core_producer = True
    worker._v39_prioritize_signals = strategic_prioritize
