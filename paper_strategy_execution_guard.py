from __future__ import annotations

import copy
import logging
from typing import Any

import paper_strategy_economics as economics


log = logging.getLogger("paper-strategy-execution-guard")
_INSTALLED = False


def _value(signal: Any, name: str, default: Any = None) -> Any:
    if isinstance(signal, dict):
        return signal.get(name, default)
    return getattr(signal, name, default)


def _with_optimizer_target(signal: Any, target: float) -> Any:
    """Return a shallow signal copy with the paper optimizer target adjusted.

    The source signal is never mutated, preserving decision/audit provenance.
    """
    if isinstance(signal, dict):
        clone = dict(signal)
        if "v39_optimizer_approved_amount" in clone:
            clone["v39_optimizer_approved_amount"] = target
        allocation = clone.get("v39_optimizer_allocation")
        if isinstance(allocation, dict):
            copied = dict(allocation)
            copied["amount"] = min(float(copied.get("amount") or target), target)
            clone["v39_optimizer_allocation"] = copied
        return clone

    try:
        clone = copy.copy(signal)
        if hasattr(clone, "v39_optimizer_approved_amount"):
            setattr(clone, "v39_optimizer_approved_amount", target)
        allocation = getattr(clone, "v39_optimizer_allocation", None)
        if isinstance(allocation, dict):
            copied = dict(allocation)
            copied["amount"] = min(float(copied.get("amount") or target), target)
            setattr(clone, "v39_optimizer_allocation", copied)
        return clone
    except Exception:
        return signal


def install_paper_strategy_execution_guard() -> bool:
    """Install paper-only fee-aware entry filtering and adaptive strategy sizing.

    This wrapper is intentionally installed after the optimizer-size handoff so it
    governs the final BUY/ACCUMULATE entry path. It never changes SELL/EXIT/CLOSE,
    quote integrity, accounting, or live-capital controls.
    """
    global _INSTALLED
    if _INSTALLED:
        return True
    if not economics.active():
        return False

    import oracle_bot

    original_buy = oracle_bot._buy

    def guarded_buy(
        market: str,
        symbol: str,
        price: float,
        signal: Any,
        quant_assessment: Any | None = None,
        target_trade_value: float | None = None,
        rotation_candidate: dict[str, Any] | None = None,
        verified_quote: dict[str, Any] | None = None,
        rotation_verified_quote: dict[str, Any] | None = None,
    ):
        if not economics.active() or str(market or "").strip().lower() != "crypto":
            return original_buy(
                market, symbol, price, signal,
                quant_assessment=quant_assessment,
                target_trade_value=target_trade_value,
                rotation_candidate=rotation_candidate,
                verified_quote=verified_quote,
                rotation_verified_quote=rotation_verified_quote,
            )

        allowed, edge_reason, edge, cost = economics.fee_edge_allows_entry(signal)
        if not allowed:
            log.info(
                "PAPER FEE AWARE ENTRY | symbol=%s | allowed=False | expected_edge_pct=%s | "
                "estimated_round_trip_cost_pct=%.4f | reason=%s | mode=paper | broker_submission=NONE | live_trading=DISARMED",
                str(symbol or "").upper(),
                "unknown" if edge is None else f"{edge:.4f}",
                cost,
                edge_reason,
            )
            return False

        optimizer_target = float(_value(signal, "v39_optimizer_approved_amount", 0.0) or 0.0)
        base_target = optimizer_target if optimizer_target > 0 else float(target_trade_value or 0.0)
        adjusted_signal = signal
        adjusted_target = target_trade_value
        if base_target > 0:
            sized, scorecard, size_reason = economics.adjusted_optimizer_target(signal, base_target)
            economics.log_economics(
                scorecard,
                symbol=symbol,
                original_target=base_target,
                adjusted_target=sized,
                reason=size_reason,
            )
            if sized <= 0:
                return False
            if optimizer_target > 0:
                adjusted_signal = _with_optimizer_target(signal, sized)
            else:
                adjusted_target = sized

        log.info(
            "PAPER FEE AWARE ENTRY | symbol=%s | allowed=True | expected_edge_pct=%s | estimated_round_trip_cost_pct=%.4f | "
            "reason=%s | mode=paper | broker_submission=NONE | live_trading=DISARMED",
            str(symbol or "").upper(),
            "unknown" if edge is None else f"{edge:.4f}",
            cost,
            edge_reason,
        )
        return original_buy(
            market, symbol, price, adjusted_signal,
            quant_assessment=quant_assessment,
            target_trade_value=adjusted_target,
            rotation_candidate=rotation_candidate,
            verified_quote=verified_quote,
            rotation_verified_quote=rotation_verified_quote,
        )

    oracle_bot._buy = guarded_buy
    _INSTALLED = True
    log.info(
        "PAPER STRATEGY EXECUTION GUARD | active=True | fee_aware_entry=ENFORCED_WHEN_EDGE_AVAILABLE | "
        "adaptive_strategy_sizing=ENABLED | exploration_floor=PRESERVED | final_churn_and_capacity_guards=DOWNSTREAM | "
        "broker_submission=NONE | live_trading=DISARMED"
    )
    return True
