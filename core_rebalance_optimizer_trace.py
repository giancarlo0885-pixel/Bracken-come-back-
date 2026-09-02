from __future__ import annotations

from typing import Any

import runtime_integrity_patch as patch
from global_pit_engine import hard_risk_gate
from strategic_rebalance_optimizer_bridge import _strategic_rebalance_gate


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
            if (
                str(market or "").lower() == "crypto"
                and patch._core_rebalance_intent(signal) == patch.CORE_REBALANCE_CANDIDATE_INTENT
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
                candidates = [
                    item for item in opportunities or []
                    if item.get("core_rebalance_candidate") is True
                    or str(item.get("portfolio_intent") or "").upper() == patch.CORE_REBALANCE_CANDIDATE_INTENT
                ]
                if candidates:
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
                        "CORE_REBALANCE_OPTIMIZER | candidates=%s | hard_gate=%s | gate_scope=TACTICAL | "
                        "strategic_authorization=%s | allocations=%s | rejections=%s | cash=%s | equity=%s | positions=%s",
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
                        hard_gate_evidence,
                        strategic_authorization_evidence,
                        plan.get("allocations"),
                        plan.get("rejections"),
                        portfolio.get("cash"),
                        portfolio.get("equity") or portfolio.get("total_equity"),
                        len(positions or []),
                    )
            return plan

        traced_optimizer._oracle_core_optimizer_trace = True
        worker.adaptive_portfolio_optimizer = traced_optimizer


# Production observability contract: tactical and strategic rebalance gates are traced separately.
