from __future__ import annotations

from typing import Any

from config import MIN_TRADE_VALUE
from crypto_opportunity_engine import crypto_core_rebalance_plan
import runtime_integrity_patch as patch


_STRATEGIC_ENTRY_ACTIONS = {"HOLD", "BUY", "STRONG_BUY", "ACCUMULATE", "LONG"}
_MEANINGFUL_ENTRY_PCT = 0.01


def _row_symbol(row: dict[str, Any]) -> str:
    return str(row.get("Asset") or row.get("symbol") or "").upper().strip()


def _reset_previous_strategic_approval(signal: Any) -> bool:
    """Expire optimizer approval metadata before a new strategic allocation pass."""
    source = str(patch._signal_value(signal, "core_rebalance_source", "") or "")
    if source != "configured_core_allocation_gap":
        return False
    if patch._core_rebalance_intent(signal) != patch.CORE_REBALANCE_BUY_INTENT:
        return False

    original_action = str(patch._signal_value(signal, "v39_original_action", "") or "").upper().strip()
    normalization_reason = str(patch._signal_value(signal, "v39_normalization_reason", "") or "").upper().strip()
    current_action = str(patch._signal_value(signal, "action", "") or "").upper().strip()
    if original_action == "HOLD" and normalization_reason == patch.CORE_REBALANCE_BUY_INTENT and current_action == "ACCUMULATE":
        patch._set_signal_value(signal, "action", "HOLD")

    for field in ("rebalance_intent", "execution_intent", "portfolio_intent", "v39_intent"):
        value = str(patch._signal_value(signal, field, "") or "").upper().strip()
        if value == patch.CORE_REBALANCE_BUY_INTENT:
            patch._set_signal_value(signal, field, "")

    patch._set_signal_value(signal, "v39_optimizer_approved_amount", None)
    patch._set_signal_value(signal, "v39_optimizer_allocation", {})
    patch._set_signal_value(signal, "v39_rebalance_approved_amount", None)
    return True


def _optimizer_decision(worker: Any, signal: Any) -> dict[str, Any]:
    symbol = str(patch._signal_value(signal, "symbol", "") or "").upper().strip()
    return dict((getattr(worker, "_core_rebalance_optimizer_decisions", {}) or {}).get(symbol) or {})


def _effective_meaningful_floor(worker: Any, signal: Any) -> float:
    allocation = patch._signal_value(signal, "v39_optimizer_allocation", {}) or {}
    decision = _optimizer_decision(worker, signal)
    for value in (
        allocation.get("meaningful_entry_floor"),
        decision.get("meaningful_entry_floor"),
        patch._signal_value(signal, "core_meaningful_entry_floor", None),
    ):
        floor = patch._numeric(value, default=0.0)
        if floor > 0:
            return floor
    return 0.0


def _promotion_rejection_reason(worker: Any, signal: Any) -> str:
    intent = patch._core_rebalance_intent(signal)
    raw_amount = patch._signal_value(signal, "v39_optimizer_approved_amount", None)
    approved_amount = patch._numeric(raw_amount, default=0.0)
    allocation = patch._signal_value(signal, "v39_optimizer_allocation", {}) or {}
    symbol = str(patch._signal_value(signal, "symbol", "") or "").upper().strip()
    allocation_symbol = str(allocation.get("symbol") or "").upper().strip()
    target_amount = patch._numeric(patch._signal_value(signal, "core_target_amount", None), default=0.0)
    meaningful_floor = _effective_meaningful_floor(worker, signal)
    decision = _optimizer_decision(worker, signal)

    if intent == patch.CORE_REBALANCE_BUY_INTENT:
        if approved_amount <= 0:
            return "stale_buy_intent_without_positive_optimizer_amount"
        if not symbol:
            return "stale_buy_intent_without_signal_symbol"
        if allocation_symbol != symbol:
            return f"stale_buy_intent_allocation_symbol_mismatch:{allocation_symbol or 'missing'}"
        return "approved"
    if intent not in {patch.CORE_REBALANCE_CANDIDATE_INTENT, patch.CORE_REBALANCE_STRATEGIC_CANDIDATE_INTENT}:
        return f"intent_changed:{intent or 'missing'}"
    if approved_amount <= 0:
        optimizer_reason = str(decision.get("reason") or "").strip().lower().replace(" ", "_")
        if optimizer_reason in {"hard_risk_gate", "capital_engine_mismatch", "not_capital_qualified", "liquidity_capacity", "max_position_reached", "no_capital_capacity", "sector_concentration_limit", "zero_executable_liquidity"}:
            return optimizer_reason
        if target_amount > 0 and meaningful_floor > 0 and target_amount + 1e-9 < meaningful_floor:
            return "target_gap_below_meaningful_entry_floor"
        if optimizer_reason:
            return optimizer_reason
        if target_amount > 0:
            return "optimizer_rejected_candidate"
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
    decision = _optimizer_decision(worker, signal)
    reason = _promotion_rejection_reason(worker, signal)
    approved = intent == patch.CORE_REBALANCE_BUY_INTENT and approved_amount > 0 and bool(symbol) and allocation_symbol == symbol

    worker.log.info(
        "CORE_REBALANCE_PROMOTION_DECISION | symbol=%s | approved=%s | intent=%s | approved_amount_raw=%s | "
        "approved_amount=%.2f | allocation_symbol=%s | target_amount=%.2f | meaningful_entry_floor=%.2f | "
        "entry_floor_mode=%s | optimizer_status=%s | optimizer_reason=%s | action=%s | reason=%s",
        symbol,
        approved,
        intent or "missing",
        raw_amount,
        approved_amount,
        allocation_symbol or "missing",
        patch._numeric(patch._signal_value(signal, "core_target_amount", None), default=0.0),
        _effective_meaningful_floor(worker, signal),
        allocation.get("entry_floor_mode") or decision.get("entry_floor_mode") or "legacy_producer_floor",
        decision.get("status") or "missing",
        decision.get("reason") or "missing",
        patch._signal_value(signal, "action", ""),
        reason,
    )


def install_strategic_core_rebalance_producer(worker: Any) -> None:
    """Authorize V39 core-rebalance candidates from configured portfolio deficits."""
    original = worker._v39_prioritize_signals
    if getattr(original, "_oracle_strategic_core_producer", False):
        return

    def strategic_prioritize(market: str, signals: list[Any], prices: dict[str, Any], ranked: list[dict[str, Any]], scan_type: str) -> list[Any]:
        if str(market or "").strip().lower() == "crypto" and signals and prices:
            for signal in signals:
                if _reset_previous_strategic_approval(signal):
                    worker.log.info(
                        "CORE_REBALANCE_APPROVAL_EXPIRED | symbol=%s | reason=new_optimizer_pass_required",
                        str(patch._signal_value(signal, "symbol", "") or "").upper().strip(),
                    )

            try:
                portfolio, positions = worker._v39_position_rows(market)
                plan_rows = crypto_core_rebalance_plan(prices, portfolio, positions)
            except Exception as exc:
                worker.log.info("CORE_REBALANCE_STRATEGIC_PLAN_BLOCKED | reason=%s", exc.__class__.__name__)
                portfolio = {}
                plan_rows = []

            equity = max(0.0, patch._numeric((portfolio or {}).get("equity"), default=0.0))
            producer_floor = max(max(0.0, patch._numeric(MIN_TRADE_VALUE, default=0.0)), equity * _MEANINGFUL_ENTRY_PCT)
            plan_by_symbol = {_row_symbol(row): row for row in plan_rows if _row_symbol(row) and patch._numeric(row.get("Amount")) > 0}
            for signal in signals:
                symbol = str(patch._signal_value(signal, "symbol", "") or "").upper().strip()
                row = plan_by_symbol.get(symbol)
                if row is None:
                    continue

                action = str(patch._signal_value(signal, "action", "HOLD") or "HOLD").upper().strip()
                if action not in _STRATEGIC_ENTRY_ACTIONS:
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
                patch._set_signal_value(signal, "core_meaningful_entry_floor", producer_floor)
                worker.log.info(
                    "CORE_REBALANCE_STRATEGIC_CANDIDATE | symbol=%s | target_amount=%.2f | target_weight=%s | current_core_value=%.2f | producer_review_floor=%.2f | action=%s",
                    symbol,
                    patch._numeric(row.get("Amount")),
                    row.get("Target Weight"),
                    patch._numeric(row.get("Current Core Value")),
                    producer_floor,
                    action,
                )

        ordered = original(market, signals, prices, ranked, scan_type)
        if str(market or "").strip().lower() == "crypto":
            for signal in ordered or []:
                _log_promotion_decision(worker, signal)
        return ordered

    strategic_prioritize._oracle_strategic_core_producer = True
    worker._v39_prioritize_signals = strategic_prioritize
