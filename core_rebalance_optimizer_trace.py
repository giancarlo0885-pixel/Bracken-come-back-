from __future__ import annotations

from typing import Any

import global_adaptive_engine as adaptive
import runtime_integrity_patch as patch
from global_pit_engine import hard_risk_gate
from strategic_rebalance_optimizer_bridge import _strategic_rebalance_gate


_ENTRY_ACTIONS = {"BUY", "STRONG_BUY", "STRONG BUY", "ACCUMULATE", "LONG"}


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _fallback_entry_decision(item: dict[str, Any]) -> dict[str, Any]:
    """Explain an entry idea even when the optimizer returned no explicit row."""
    qualified = item.get("qualified_for_capital") is True
    if not qualified:
        return {
            "status": "REJECTED",
            "reason": "not_capital_qualified",
            "qualified_for_capital": item.get("qualified_for_capital"),
        }

    capital_engine = adaptive.classify_capital_engine(item)
    if capital_engine != "crypto":
        return {
            "status": "REJECTED",
            "reason": "capital_engine_mismatch",
            "capital_engine": capital_engine,
            "qualified_for_capital": True,
        }

    gate = hard_risk_gate(item)
    if not gate.get("allowed"):
        return {
            "status": "REJECTED",
            "reason": "hard_risk_gate",
            "risk_reasons": gate.get("reasons") or [],
            "core_signals_supporting": gate.get("core_signals_supporting"),
            "confidence_score": gate.get("confidence_score"),
            "reward_risk_ratio": gate.get("reward_risk_ratio"),
        }

    return {
        "status": "REJECTED",
        "reason": "optimizer_not_selected_after_eligibility",
        "qualified_for_capital": True,
    }


def _optimizer_decision_index(
    opportunities: list[dict[str, Any]],
    plan: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Build a read-only per-symbol decision view for downstream observability."""
    decisions: dict[str, dict[str, Any]] = {}

    for rejection in plan.get("rejections") or []:
        symbol = _upper(rejection.get("symbol"))
        if not symbol:
            continue
        decisions[symbol] = {
            "status": "REJECTED",
            "reason": rejection.get("reason") or "optimizer_rejected_candidate",
            "risk_reasons": rejection.get("risk_reasons") or [],
            "capacity_reason": rejection.get("capacity_reason"),
            "watch_only": bool(rejection.get("watch_only")),
            "proposed_amount": rejection.get("proposed_amount"),
            "meaningful_entry_floor": rejection.get("meaningful_entry_floor"),
            "entry_floor_mode": rejection.get("entry_floor_mode"),
            "capital_engine": rejection.get("capital_engine"),
            "qualified_for_capital": rejection.get("qualified_for_capital"),
        }

    for allocation in plan.get("allocations") or []:
        symbol = _upper(allocation.get("symbol"))
        if not symbol:
            continue
        decisions[symbol] = {
            "status": "APPROVED",
            "reason": "capital_allocated",
            "approved_amount": allocation.get("amount"),
            "meaningful_entry_floor": allocation.get("meaningful_entry_floor")
            or plan.get("meaningful_entry_floor"),
            "entry_floor_mode": allocation.get("entry_floor_mode")
            or plan.get("meaningful_entry_floor_mode"),
        }

    for item in opportunities or []:
        symbol = _upper(item.get("symbol"))
        action = _upper(item.get("action") or item.get("tactical_action"))
        if not symbol or action not in _ENTRY_ACTIONS or symbol in decisions:
            continue
        decisions[symbol] = _fallback_entry_decision(item)

    return decisions


def install_core_rebalance_optimizer_trace(worker: Any) -> None:
    """Read-only tracing for the candidate -> V39 -> optimizer boundary."""
    original_opportunity = worker._v39_signal_opportunity
    if not getattr(original_opportunity, "_oracle_core_gate_trace", False):
        def traced_opportunity(
            market: str,
            signal: Any,
            prices: dict[str, Any],
            ranked_by_symbol: dict[str, dict[str, Any]],
            scan_type: str,
        ) -> dict[str, Any]:
            opportunity = original_opportunity(market, signal, prices, ranked_by_symbol, scan_type)
            if str(market or "").lower() == "crypto" and (
                patch._core_rebalance_intent(signal) == patch.CORE_REBALANCE_CANDIDATE_INTENT
                or _upper(opportunity.get("action") or opportunity.get("tactical_action")) in _ENTRY_ACTIONS
            ):
                worker.log.info(
                    "CORE_REBALANCE_V39 | symbol=%s | qualified=%s | risk_score=%s | risk_known=%s | "
                    "quote_verified=%s | identity_verified=%s | execution_fresh=%s | tradeable=%s | "
                    "liquidity=%s | spread_pct=%s | signal_id=%s | forecast_id=%s | stages=%s",
                    str(opportunity.get("symbol") or patch._signal_value(signal, "symbol", "")).upper(),
                    opportunity.get("qualified_for_capital"),
                    opportunity.get("risk_score"),
                    opportunity.get("risk_known"),
                    opportunity.get("quote_verified"),
                    opportunity.get("identity_verified"),
                    opportunity.get("execution_fresh"),
                    opportunity.get("tradeable"),
                    opportunity.get("avg_dollar_volume") or opportunity.get("liquidity"),
                    opportunity.get("spread_pct"),
                    bool(opportunity.get("signal_id")),
                    bool(opportunity.get("forecast_id")),
                    opportunity.get("stages"),
                )
            return opportunity

        traced_opportunity._oracle_core_gate_trace = True
        worker._v39_signal_opportunity = traced_opportunity

    original_optimizer = worker.adaptive_portfolio_optimizer
    if not getattr(original_optimizer, "_oracle_core_optimizer_trace", False):
        def traced_optimizer(
            opportunities: list[dict[str, Any]],
            portfolio: dict[str, Any],
            positions: list[dict[str, Any]],
            *,
            engine: str,
        ) -> dict[str, Any]:
            plan = original_optimizer(opportunities, portfolio, positions, engine=engine)
            if str(engine or "").lower() == "crypto":
                decisions = _optimizer_decision_index(opportunities, plan)
                worker._core_rebalance_optimizer_decisions = decisions

                candidates = [
                    item for item in opportunities or []
                    if item.get("core_rebalance_candidate") is True
                    or str(item.get("portfolio_intent") or "").upper() == patch.CORE_REBALANCE_CANDIDATE_INTENT
                ]
                entry_ideas = [
                    item for item in opportunities or []
                    if _upper(item.get("action") or item.get("tactical_action")) in _ENTRY_ACTIONS
                ]
                if candidates or entry_ideas:
                    hard_gate_evidence = []
                    strategic_authorization_evidence = []
                    for item in candidates:
                        tactical_gate = hard_risk_gate(item)
                        strategic_gate = _strategic_rebalance_gate(item)
                        hard_gate_evidence.append(
                            {
                                "symbol": item.get("symbol"),
                                "allowed": tactical_gate.get("allowed"),
                                "reasons": tactical_gate.get("reasons"),
                                "core_signals_supporting": tactical_gate.get("core_signals_supporting"),
                                "confidence_score": tactical_gate.get("confidence_score"),
                                "reward_risk_ratio": tactical_gate.get("reward_risk_ratio"),
                            }
                        )
                        strategic_authorization_evidence.append(
                            {
                                "symbol": item.get("symbol"),
                                "allowed": strategic_gate.get("allowed"),
                                "reasons": strategic_gate.get("reasons"),
                                "tactical_authorization_reasons": strategic_gate.get("tactical_authorization_reasons") or [],
                                "authorization_basis": strategic_gate.get("authorization_basis")
                                or ("hard_risk_gate" if strategic_gate.get("allowed") else "blocked_by_hard_risk_gate"),
                            }
                        )
                    worker.log.info(
                        "CORE_REBALANCE_OPTIMIZER | candidates=%s | entry_ideas=%s | hard_gate=%s | gate_scope=TACTICAL | "
                        "strategic_authorization=%s | allocations=%s | rejections=%s | decisions=%s | cash=%s | equity=%s | positions=%s",
                        [
                            {
                                "symbol": item.get("symbol"),
                                "qualified": item.get("qualified_for_capital"),
                                "risk_score": item.get("risk_score"),
                                "liquidity": item.get("avg_dollar_volume") or item.get("liquidity"),
                                "spread_pct": item.get("spread_pct"),
                            }
                            for item in candidates
                        ],
                        [
                            {
                                "symbol": item.get("symbol"),
                                "action": _upper(item.get("action") or item.get("tactical_action")),
                                "decision": decisions.get(_upper(item.get("symbol"))),
                            }
                            for item in entry_ideas[:12]
                        ],
                        hard_gate_evidence,
                        strategic_authorization_evidence,
                        plan.get("allocations"),
                        plan.get("rejections"),
                        decisions,
                        portfolio.get("cash"),
                        portfolio.get("equity") or portfolio.get("total_equity"),
                        len(positions or []),
                    )
            return plan

        traced_optimizer._oracle_core_optimizer_trace = True
        worker.adaptive_portfolio_optimizer = traced_optimizer


# Production observability contract: tactical and strategic rebalance gates are traced separately.
# Railway deploy-watch verification marker: every crypto entry idea gets a decision reason.
